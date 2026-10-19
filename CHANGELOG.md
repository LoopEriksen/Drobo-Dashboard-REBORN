# Changelog

Every release lists what changed since the one before, so anyone building on
this can see what moved. Dates are the day the work landed, not the day it was
released. Every push to this repository carries this file.

## Unreleased

### Fixed
- **A status-channel test can no longer pass without testing what it
  names.** Three socket cases in `sdk/tests/test_esatm.py` sent from
  threads that looked their socket up when they ran, not when they were
  started, so a sender slow to run could close the next case's socket
  instead of its own. The bad-signature case then passed on a hang-up,
  because a short header raises the same error class. The cases now send
  before they read, from the test itself, as `test_config_cmd.py`
  already did, and the bad-signature check requires the bad-signature
  error. Tests only: nothing the software sends to a Drobo changes, and
  the suite still has 60 checks. (2026-10-19)
- **The capture dissector finishes instead of crashing.**
  `tools/nasd_dissect.py` stopped with a Python `NameError` whenever it
  could not work out a frame layout. That happened on an empty capture, on
  any recording of the command port (5001), which
  `tools/capture-identify.ps1` asks it to read, and on the status port's
  real greeting, whose confirmed layout is outside the shapes it tries. So
  its `-o` report was never written. It now prints its "no framing could
  be inferred" note and finishes, and it names the port it looked on
  instead of always saying 5000. It still works out no layout from either
  port's real traffic; `tools/extract_commands.py` is the tool for the
  command port. Its test suite gains seven checks for this (51 to 58).
  (2026-10-19)
- **The pairing QR encoder states its real limit.** Its notes and its
  error message said it held about 270 characters. The most it holds is
  213 bytes (version 10 at error-correction level M, in byte mode), and
  the error now says so along with the size it was given. The pairing
  link is well inside that, so no QR code changes. `test_agent.py` gains
  three checks for this (268 to 271): a 213-byte round trip, which is the
  first to reach version 10, the stated limit, and the refusal of 214
  bytes. (2026-10-19)
- **Uptime no longer runs fast or slow when one reading is missing.**
  The Drobo reports its temperature and uptime in one reply, and the
  agent counted uptime on from the time of the last *temperature*
  reading. A reply with an uptime but no usable temperature (one outside
  1-95 C is dropped as implausible) therefore counted the time since
  that temperature twice, and more with every such reply. A reply with a
  temperature but no uptime restarted an old uptime from that moment, so
  it read low. Each reading now keeps its own time, and the status no
  longer gives a reading time to a temperature that never arrived.
  Nothing sent to the Drobo changed, nor how often. `test_drivers.py`
  gains seven checks for this (47 to 54). (2026-10-19)
- **Four SDK readers no longer test an XML element's truth value.** The
  readers for the Drobo's temperature and uptime, its throughput, its
  DroboApps list and its shop-demo flag each found their section with
  `find(...) or` the enclosing element. An element with no children
  counts as false, so an empty section fell back to the one around it,
  and Python 3.12 and later warn on every such test. With that warning
  made an error (as `-W error` or `PYTHONWARNINGS=error` does), a
  reply whose section was not escaped stopped the reader with an
  exception; from the temperature reader, that would have shown the
  Drobo as unreachable. They now test `is not None`, which also matches
  what Python 3.13 and later announce: that an element will always
  count as true. The Drobo's real replies escape these sections, so no
  value read from it changes, and nothing sent to it changes.
  `test_sysinfo.py` gains five checks for this (45 to 50). (2026-10-19)
- **A config.json that is not UTF-8 is reported, not misread.** One saved
  as UTF-16 (Windows PowerShell 5.1's `>` and Out-File write that, and
  Notepad does when asked) stopped the agent at start-up with a raw
  traceback, and the Check for Updates shortcut said update checks were
  off, with exit status 0, even when the file switched them on. Both now
  say the file is not UTF-8 text and to save it as UTF-8, and the
  shortcut exits 3, as it does for a file that is not valid JSON.
  Separately, `py -m drobo_agent.updates` exits 2 (could not tell), not
  1 (an update is available), on a config.json it cannot read; the
  shortcut, which runs `check_updates.py`, keeps its 3. A file of valid
  JSON but the wrong shape is handled as before. The comment in the
  update check that described how a missing or broken config was handled
  had been wrong for both and now says what happens. `test_updates.py`
  gains two checks for this (77 to 79). (2026-10-19)
- **`tools/fwtool.py` names JFFS2 compression types correctly.** When it
  met firmware nodes it cannot unpack, it labelled type 8 as LZO. In
  JFFS2's own numbering (`linux/jffs2.h`) LZO is 7, so real LZO nodes were
  reported as a bare "7", and type 8, which has no mainline meaning, as
  LZO. It is a research tool for firmware files and never contacts a
  Drobo. (2026-10-19)
- **Comments that no longer matched the code**, corrected with no change
  in behaviour: the example alert key (`drive-failed-3`, not
  `drive-failed-bay3`) in the agent's `models.py` and the Windows app's
  `AlertRow.cs`; and the ways discovery can find a Drobo, which also
  include an address typed in (`manual`) and the one already in use
  (`in-use`), and which front-ends receive as `found_by`. (2026-10-19)

### Changed
- **The project is now free software: GPL-3.0-or-later.** Until now no
  licence had been granted and all rights were reserved. The full text is
  `LICENSE` at the top of the repository, the README's License section says
  the same, and the SDK's package metadata declares it (it said
  "Proprietary" before). The installer now puts a copy of `LICENSE` in the
  program folder, beside `TESTING.md`. (2026-10-19)

### Removed
- **Four pieces of code that nothing used.** An unused `_text` helper in
  `sdk/drobo_nasd/drobosync.py`, a copy of the photo settings that
  `agent/drobo_agent/photos.py` stored and never read, three palette
  colours the icon generator no longer draws with, and an unused helper
  in `sdk/tests/test_discovery.py`. The DroboSync module's notes also
  said its field names were read defensively. No field is named there:
  every value in a reply is read generically, and the notes now say so.
  Nothing the software does or sends to a Drobo changes, the icons are
  byte-for-byte the same, and no suite's check count moves. (2026-10-19)

## 1.1.0 -- 2026-10-15

Everything below has landed since 1.0.0.

**Before you install 1.1.0: uninstall 1.0.0 first.** 1.1.0 installs into
Program Files for everyone on the PC, and 1.0.0 installed into your own user
profile; Windows Installer cannot upgrade one of those in place with the
other, so installing 1.1.0 over it leaves two copies that do not know about
each other. Remove 1.0.0 from Settings > Apps, then install 1.1.0. Your
settings and access token are kept: they were never in the program folder.
(Why, under Changed below.)

**Where releases come from now.** 1.1.0 and every later release are published
on this repository's own Releases page. 1.0.0 came from a separate, code-only
copy of this repository, which is retired and will get no further releases:
on 2026-10-08 this repository itself became the public one. The advice above
does not change -- uninstall 1.0.0 first, wherever you got it.

### Added
- **Every push runs the tests by itself.** The repository now carries a
  workflow that runs all thirty Python suites and the Windows app's own checks
  on every push, on Windows, as well as the secrets-and-personal-data scan and
  the compile check it already ran. A release is no longer the first time they
  have all been run together. (First green run on GitHub 2026-09-20, both
  jobs.)
- **The privacy guard knows the Drobo's own identifiers.** The scanner
  (`scripts/scan_for_pii.py`) recognises the shape of a Drobo serial number
  and a Drobo MAC address alongside the user-folder paths and email addresses
  it already caught, and masks what it finds instead of reprinting it.
- **Settings shows versions.** The Drobo page under Settings now lists this
  app's version and the connected Drobo's firmware, beside its network
  configuration. The firmware line follows every status reading, so it names
  the Drobo actually connected, and says so when none is.
- **The simulated Drobo has an account: user `admin`, password `admin`.** It
  has read/write access to every simulated share, including Private, where
  Everyone has none. With no account, "Connect as..." was greyed out and the
  sign-in flow could not be tried without hardware. When the app is attached to
  the simulator, the Connect dialog asks the simulator to check the login
  (`POST /api/mock/sign-in`, mock driver only) instead of Windows, because the
  simulated shares point at an address with no file server behind it. A wrong
  password fails with the same message a real Drobo would give. Nothing
  simulated is ever handed to Windows: no drive is mapped and no login is
  saved to Credential Manager, where a pretend login could shadow a real one.
- **Save a share login on this PC.** Ticking "Save this login on this PC" in
  the connect dialog stores the Drobo username and password in Windows'
  Credential Manager, encrypted by Windows under your Windows account, so you
  are not asked again -- in this app or in File Explorer. It is optional and
  off unless ticked, it can be removed from the dialog or from Control Panel,
  and this project still writes no password file of its own and invents no
  encryption. The box used to be greyed out unless you were mapping a drive
  letter, because the only mechanism then was a flag Windows applies to mapped
  drives and silently ignores otherwise; it now works either way.
- **Configuration writes**, the first changes this software makes to the
  device: LED brightness and drive spin-down delay, written as a
  read-modify-write of the device's live settings block that refuses if the
  block holds an unrecognised field.
- **Per-user share access** writes, verified on hardware. Only the confirmed
  read/write level (code 1), code 0 (most likely "no access", but not
  confirmed -- written because no access is the safe direction to be wrong
  in), or a level already set on the device can be written; the SDK never
  invents a code.
- **Create and delete a share**, from a capture of the original Dashboard doing
  both. Delete requires the share name confirmed twice. Create has been
  applied to a real device once; delete has not yet been sent to one.
- **Start / stop an installed DroboApp**, validated against the device's own
  list. Built from a capture of the original Dashboard; this software has not
  yet sent it to a real device.
- **The device's event log** (`eCmdGetUserEventLog`) fetched and shown in both
  the Windows app and the browser dashboard. The reply's per-line timestamp
  and severity format is still undecoded, so those fields are marked as not
  decoded rather than invented.
- **PSU and fan alerts**, plus an end-to-end notification simulation so the
  alert path can be exercised without a fault on real hardware.
- **Advanced Configuration** panel in the Windows app, behind the admin login;
  a **health card**, **event log viewer**, and **per-share user lists**.
- **In-app file browser** for a mounted share, and an explicit mount flow.
- Browser dashboard brought to **write parity** with the Windows app.
- A **PII scanner** and pre-commit guard for the repository.
- `docs/testers-tasks.md`: what needs hardware the maintainer does not have,
  with read-only steps a tester can run and report.
- **An installer testers can actually use.** The MSI now bundles its own
  Python runtime (the embeddable 3.14 distribution from python.org, version
  and SHA-256 pinned in `windows/installer/python-embed.lock`), so nothing
  else needs installing. The agent's `config.json` -- with the access token
  it generates -- lives in `%LOCALAPPDATA%\DroboDashboardReborn`, so an
  uninstall removes the program folder completely (including the files the
  agent generated in it), while your settings are kept the way most apps
  keep them, and a reinstall or upgrade picks up where you left off. A Start
  Menu **Uninstall** entry. `TESTING.md` ships inside the install folder.
  The lifecycle test (`windows/test-installer.ps1`) runs the installed
  product through its own launcher against the simulated driver, waits for
  the agent to answer, checks the token gate, and -- given a second, higher-
  numbered MSI -- proves an upgrade keeps user data and wipes the old
  program files.
- `docs/capabilities.md`: what the software is meant to do and how far each
  part has got, every row checked against the code; and
  `docs/development-history.md`: how it was built, in order, including the
  turns that were wrong. Both ship with each release. Every other project
  document now points at them.
- This changelog.

### Fixed
- **The agent no longer stops when it cannot find the Drobo at start-up.**
  With automatic discovery, the default, an agent that found no Drobo when it
  started exited before serving anything: a Drobo that was off or still
  booting, or a PC whose own network was not up yet, meant no dashboard, no
  alerts and no monitoring -- and an agent started at boot stayed that way.
  It now starts anyway, says it cannot reach the Drobo, and looks again on
  every poll; when the Drobo appears it is picked up with no restart. A
  device that answers but is not your Drobo still stops the agent, now with a
  plain message naming both serial numbers instead of a Python traceback, and
  a fixed address that answers with something other than a Drobo is treated
  like any other unreachable Drobo instead of ending in one. The message the
  agent's window shows when it does stop is updated to match.
- **Something on the network that answers but never finishes replying can no
  longer stall the app.** A socket timeout bounds each read, not the whole
  read, so anything on the network that answered and then trickled one byte
  at a time could hold a frame open for hours -- on the status channel that
  was enough to wedge the device picker until a restart, because the agent
  holds the discovery lock across that read. The status channel, and the
  settings reads and writes on the command channel, now give each reply a
  time limit that covers the header and the payload together. The other
  command-channel requests -- temperature and uptime, the event log,
  Identify, DroboApps and DroboSync -- do not have it yet and still rely on
  the per-read timeout alone. A device that answers normally is unaffected.
- **The share-permission help text no longer calls code 0 confirmed.** Both
  the Windows app and the browser dashboard said "only the two confirmed
  levels are offered: 1 = read/write and 0 = no access". Only code 1 has
  been confirmed against the original Dashboard. Code 0 is most likely no
  access (every share seen with it was unreachable), and the text now says
  exactly that, as does the README's list of what the software writes.
  What can be set has not changed.
- **A build made with `-Version` now says that version everywhere.** The
  option changed the package, the folder and `version.txt` but never reached
  the compiled program, so a release built that way would have reported a
  version its own Add/Remove Programs entry disagreed with -- and Windows
  Installer compares that file version when deciding whether to replace a
  file. A build with no `-Version` is byte-for-byte what it was. A new check
  (`scripts/check_versions.py`, also run by the test sweep) compares the seven
  places the release number is written and fails if any of them disagree.
- **The installer's testing guide no longer describes a version that was
  never released.** It told testers that "versions up to 1.0.1" installed
  into their user profile; the only release so far is 1.0.0, and the package
  became per-machine on 2026-09-08. The upgrade examples now read as
  examples rather than as a build anyone can get.
- **The iOS entry in the testers' task list explains itself.** It pointed at
  a file that the separate, code-only public copy of the time did not ship.
  It now says the substance:
  no third-party iOS app may run continuously in the background, so an iOS
  client would back up on arriving home and catch up overnight while
  charging -- a platform limit, not an agent limit -- and there is nothing
  for a tester to run today.
- **The simulated Drobo gets a random access token.** `START WITH SIMULATED
  DROBO (testing).bat` used to write one fixed token into the simulator's
  settings and print it, and the launcher that wrote it is a published file,
  so that value was readable by anyone -- which made it the same as no token
  at all. It was never demo-scoped either: one token opens every route on the
  agent, including the one that starts a backup. The launcher now leaves the
  token empty and lets the agent generate a random one, exactly as it already
  does for the real `config.json`, then reads that value back for the
  dashboard link and the examples it prints. A PC still carrying the old fixed
  value is moved onto a fresh random one at the next launch, with nothing else
  in those settings touched. A saved simulator bookmark with the old
  `?token=...` in it will need the new token from the launcher's banner.
- **The simulated launcher starts again.** `START WITH SIMULATED DROBO
  (testing).bat` said "The simulated agent did not start" on a PC with
  Microsoft's Python install manager. Given the agent's script, the `py`
  launcher follows its first line, `#!/usr/bin/env python3`, searches PATH for
  `python3`, and finds the install manager's Store alias first. Started hidden,
  as this launcher does, the alias cannot run and `py` exits with code 101. The
  launcher now asks `py` once where the real interpreter is and starts that
  directly. That also makes "stop the simulator when the app exits" stop the
  agent itself: it used to stop the `py` wrapper, which could leave the real
  agent holding port 7420.
- **The app connects to the simulator it was launched with.** The app finds
  its access token by looking for an agent config, and in a checkout the first
  one it finds belongs to the real agent, so the simulator refused every
  request. The launcher now names its config with `--agent-config`, and given
  that option the app reads that file and no other.
- **`START DROBO DASHBOARD.bat` connects the app to the agent it starts.** On
  a PC where the installed copy had also been used, the app took the
  installed agent's access token, and the agent this launcher starts refused
  every request. The launcher now names its agent's config with
  `--agent-config` too.
- **The agent no longer prints its access token at startup.** The request log
  was fixed on 2026-09-08, but the startup line `open http://.../?token=...`
  was missed and still printed the real token into the console that testers
  are asked to paste. It now prints the address alone. `--open` still opens the
  browser signed in, without printing the token.
- **An idle connection no longer leaves a traceback in the agent's console.**
  A connection that timed out before sending anything reached the request log
  with no path, which raised an error. It is now logged as the timeout it is.
- **The Alerts tab has a name screen readers can read.** It was announced as
  "System.Windows.Controls.TabItem Header: Content:".
- **Every button and drop-down is readable in every state.** No button or
  drop-down in the app had a template of its own, so Windows' stock one took
  over on hover, press and disable, painting light backgrounds under this
  app's light text. Drop-down lists opened white with near-white items. One
  set of app-wide templates now covers every window and dialog, with every
  colour pair measured at WCAG's 4.5 to 1 for normal text or better in every
  state, a readable disabled state, and a visible keyboard focus ring. The
  app's blue and its delete red are each a shade darker, because white on the
  old ones measured 4.4 and 3.7.
- **The fallback installer's uninstaller removes what the installer
  created.** The PowerShell installer used when WiX is unavailable became
  per-machine along with the MSI, but its uninstaller still looked in the
  per-user places. It left the all-users Start Menu and Desktop shortcuts
  and the Add/Remove Programs entry behind, and without administrator
  rights it could not delete the program folder. It now removes the same
  four things the installer creates, and asks Windows for administrator
  approval when it is started without it.
- **The fallback installer's uninstaller no longer half-removes a running
  copy.** With the app or its agent window still open, it removed the
  shortcuts and the Add/Remove Programs entry, left the program folder behind,
  and reported success, so there was nothing left to uninstall it from. It now
  says to close them first and removes nothing until they are closed.
- **The fallback installer's uninstaller leaves no empty program folder, and
  its last message can be read.** The helper that deletes the folder itself
  tried once, half a second after the script ended, and gave up silently if
  something still held it -- an exiting PowerShell, or a leftover agent window
  sitting in the folder. It now keeps trying for about twenty seconds. Started
  from the Start Menu shortcut or Settings > Apps, the window also closed the
  instant it finished, so nobody could read "Done" or the line before it
  saying the settings folder and its access token were kept; it now waits for
  Enter, exactly as its two "nothing was removed" messages already did.
- **"Check for Updates" works in an installed copy.** From the day the
  installer began bundling its own Python (2026-09-02) the Start Menu
  shortcut failed in every installed copy: it ran
  `python -m drobo_agent.updates` from inside the agent's folder, and the
  bundled Python never looks in the folder it is started from, so it stopped
  with "No module named 'drobo_agent'" -- and closed its window at once, so
  nothing was seen. 1.0.0, which used the Python already on the PC, did not
  have this fault. It now runs `agent\check_updates.py` by its path, which
  finds the agent's code itself. The window waits for a key before closing,
  whatever the answer, and a check that cannot run says why and exits 3
  rather than Python's 1, which is the check's own "an update is available".
  The installer's lifecycle test now runs the shortcut and requires its
  answer. (Proven against a copy of the bundled runtime, then in a real elevated
  install on 2026-10-08.)
- **`Check-ForUpdates.bat` also works inside a copy of the source.** That
  fault was the source copy's alone; the installed copy's is the entry
  above. Double-clicked where it sits in the source, `windows/installer/`,
  it looked for the agent in a folder that does not exist and failed without
  checking; it now uses the source's `agent/`
  folder, and the `agent/config.json` beside it: the settings path had been
  left pointing at an installed copy's file, so a run from the source read
  another install's settings, and created that install's folder and an unused
  access token when there was none.
- A write the device accepts but never acknowledges (it closes the connection
  instead of answering) is now decided by reading the device back, instead of
  being reported as a failure it was not.
- Duplicate user rows in a share's access list are refused instead of being
  half-changed.
- Spin-down "off": the device coerces a bare delay of 0, so disabling now
  clears the feature bit and zeroes the delay together.
- Share-name ambiguity and the accepted-but-unverified outcome are named
  explicitly instead of collapsing into success or failure.
- The dark scrollbar, the empty landing area, and double-click to open a
  share in the Windows app.
- A permission code that has never been observed on the wire was being
  described as "access allowed"; it is now labelled as the unknown it is.

### Privacy (the audit of 2026-09-08)

A privacy audit went looking for what this project could reveal about the
person who wrote it and the people who test it. Nothing found had reached the
separate public repository of the time, which was clean. All of it
would have shipped in the next release.

- **The Windows app no longer carries the build machine's identity.** The
  compiler records where it built, so the shipped executable embedded the full
  project path, naming a Windows account and a cloud-sync folder, and that
  string travelled to every tester inside the MSI. Fixed at the compiler
  (`PathMap`, and no PDB in a Release build). The installer build now searches
  the compiled binaries for a user-profile path and refuses to package one,
  which it could not do before: the check it already had skipped `.exe`,
  `.dll` and `.pdb`, the only files that carry this.
- **Two real share names are out of the test fixtures.** They were recorded
  from a live capture, and a scrub in August had already removed them once. They came back on 2026-08-26 in fixtures that ship with
  the code. Replaced with the same Alpha and Beta placeholders as before.
- **The access token stays out of the agent's console.** The token is accepted
  as a URL parameter on every route, and the access log printed the whole
  request line, so it appeared in the window testers are told to copy into a
  public issue when the agent stops. The log now redacts it. The token itself
  is unchanged and still works.
- **The testing guide says what a diagnostics report contains.** It said the
  report holds no passwords and no token, which was true and not the whole
  story: it also carries drive serial numbers, the device serial and, when the
  Drobo is unreachable, the PC's private addresses. Testers are asked to read
  it before pasting it into a public issue.
- **The two guards that missed all of this now work.** Name detection was
  entirely off, because the pattern list it reads is per-machine and gitignored
  and simply did not exist; both the scanner and the pre-commit hook treated
  that as success and printed "clean". They now say so loudly. The scanner also
  looks only at files git tracks, which cut 1,900 hits to nothing by dropping
  build output nobody can commit, and it no longer prints the name it finds.

### Hardening and tests (the audit; landed 2026-09-02)
- The agent's HTTP handler times out a stalled connection after 60 seconds
  instead of holding a thread for ever.
- The Windows app installs a last-resort exception handler: an error that
  escapes an event handler now shows a message and the app keeps running,
  instead of the Windows crash dialog.
- API-level tests for the brightness, spin-down and backup-plan routes, which
  had none: bad bodies are 400s, the token gate holds, simulated writes say
  so (`agent/tests/test_config_api.py`).
- A test pinning that a volume name with an XML-special character is refused
  rather than guessed at.
- The SDK's version said 0.1.0 while everything else said 1.0.0
  (`pyproject.toml` fixed 09-02; `drobo_nasd.__version__` fixed 09-04).
- `run_agent.py --open` and `--config` documented; the test-suite inventory
  in `agent/README.md` corrected (29 suites, not 21).

### Changed
- **The Windows app runs on .NET 10** (2026-10-08). It was built for .NET 8,
  whose support ends on 2026-11-10; .NET 10 is supported until November 2028.
  The installer still carries its own copy of the runtime, so there is nothing
  extra to install. Building the app from source now needs the .NET 10 SDK.
- **The installer now installs like an ordinary Windows program**, into
  `C:\Program Files\Drobo Dashboard REBORN` for everyone on the PC, instead
  of into one user's profile. Windows asks for administrator approval, which
  it did not before. Settings stay per Windows account in
  `%LOCALAPPDATA%\DroboDashboardReborn`, so two people sharing a PC keep
  separate configurations and access tokens.

  **Upgrading from 1.0.x needs the old copy uninstalled first.** Windows
  Installer cannot upgrade a per-user install with a per-machine package; the
  two live in different installation contexts and neither can see the other.
  Settings and tokens survive, having never been in the program folder.

  The PowerShell fallback installer, used only when WiX is unavailable, moved
  with it, so both paths install to the same place rather than disagreeing.
- **This software will not be code-signed** (decided 2026-09-05). It is not a
  for-profit project, and a certificate is a recurring cost that would also put
  a private individual's legal name inside every file distributed. The
  consequence is documented rather than hidden: Windows SmartScreen calls the
  publisher unknown on first run, and a machine with Smart App Control switched
  on will not run the Windows app until its owner switches that off -- which,
  since the April 2026 Windows update, no longer requires reinstalling Windows.
  The browser dashboard is unaffected either way, because the bundled Python
  carries the Python Software Foundation's own signature.
- The README's safety statement. 1.0.0 described the software as read-only
  apart from Identify; that stopped being true when the writes above landed,
  and the README now lists exactly what it will and will not change.
- The capture tooling stops a recording early on request and never leaves
  `pktmon` running; it finds the Drobo again when its address has moved.
- **The agent's test suites moved to `agent/tests/`**, beside the SDK's in
  `sdk/tests/`. `tools/`, `scripts/`, `windows/installer/` and
  `.github/workflows/` each have a README saying what is in them, and the
  agent, SDK and Windows READMEs now have an "In this folder" table.
- **The whole repository is public now** (decided 2026-10-08), documents
  included: the roadmap, the protocol reference and the research notes ship
  as they are, instead of a stripped copy with a roadmap derived from the
  working one. It starts from one fresh commit; the earlier history is kept
  privately, so commit hashes quoted in the documents will not resolve here.
  Personal and device detail was scrubbed from every file first.

## 1.0.0 -- 2026-08-10

First public release, published as a code-only copy in a separate repository
that has since been retired (see 1.1.0). The MVP bar -- find the Drobo, show
its real health and capacity, get you to your files -- met on real hardware.

### What it does
- Finds the Drobo by its permanent hardware serial (mDNS, hostname, last-known
  address, optional subnet sweep, or a typed address), and refuses anything
  else answering on that address.
- Reads live status from the device's push channel: identity, per-bay drive
  health, capacity, volumes, chassis temperature and uptime, throughput.
- Alerts, worst-first, with desktop notifications and optional ntfy and email
  channels -- all off by default.
- Lists network shares and connects as a named user, which is what Windows 11
  requires now that it refuses the Drobo's guest logon.
- Identify, the "blink the front lights" command: accepted by the device,
  though on the unit tested it produces no visible effect (established
  2026-08-07). And a list of installed DroboApps.
- Backs the Drobo up to an external drive.
- A background agent exposing all of this as a local JSON API, a browser
  dashboard, and a native Windows app with an MSI installer and a dormant
  auto-updater.
- A photo-upload API on the agent side (pairing by QR, hash-first dedupe,
  resumable chunked upload) ready for a mobile app that does not exist yet.

### Deliberately not in 1.0.0
- Any write to the device beyond Identify.
- Renaming the Drobo, creating or deleting users, firmware of any kind.
