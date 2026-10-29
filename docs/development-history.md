# How this was built

The path from a blueprint to a release, in the order it actually happened,
including the turns that were wrong. Dates are the day work landed; commit
hashes are given where a claim is worth checking. Those hashes point into the
earlier history, which is kept privately and does not resolve in the public
repository (see the 2026-10-08 section).
Where this document and the code disagree, the code is right and this needs
correcting.

Companion documents: [`capabilities.md`](capabilities.md) says what the
software is meant to do and how far each part has got; [`../ROADMAP.md`](../ROADMAP.md)
is the plan of record; [`../CHANGELOG.md`](../CHANGELOG.md) is what changed
between releases.

---

## The starting point (2026-07-22 to 23): a dead vendor, a working box, a blueprint

Drobo's parent company was liquidated in 2023. `drobo.com` no longer resolves,
both iPhone apps were pulled from the App Store, and the last Windows release
of Drobo Dashboard, 3.5.0 from December 2019, will never be updated. The
hardware still works. **The running Dashboard is now the only surviving
specification of the protocol**, and the day a Windows update breaks it, the
protocol gets much harder to recover. That single fact ordered everything
that followed: capture first, then build.

The brief was six phases. Recreate Dashboard on Windows without rewriting the
firmware; then the optional features; then an iOS app that uploads photos and
video; then a replacement for Drobo Pix; then remote access; then a public
release with all personal information scrubbed.

The blueprint that came with it, converted from a Word document, proposed the
architecture a commercial team would choose: a unified SDK in Go or Rust with
FFI bindings for Swift and C#, a WinUI 3 desktop app, a SwiftUI phone app,
firmware updates, modern TLS with token sessions, push notifications through
Apple's APNs.

A reality check on 2026-07-23 accepted five corrections the same day. Each was
recorded at the time; the short form:

- **Firmware updating was cut.** The last builds, 4.3.0 and 4.3.1, were pulled
  by Drobo's own engineers for user-reported problems; 4.2.2 is the community's
  last known good. A home-built flasher writing an unsigned image to an
  irreplaceable device with no vendor recovery tool was the one feature in the
  blueprint that could destroy the hardware. The app reads and displays the
  firmware version and warns on 4.3.x; nothing more.
- **TLS and token sessions cannot be done from the client side.** The device's
  end is frozen firmware. What can be done is LAN-only by default, credentials
  in the Windows Credential Manager, and Tailscale for remote access, which
  cannot run on the Drobo itself (its kernel predates WireGuard by eight years).
- **APNs needs a server, and the Drobo cannot be one.** Something has to be
  awake and watching. That became the agent, pushing through ntfy or email.
- **No cross-language SDK with FFI bindings.** The protocol surface is small
  and the binding toolchain would have cost months before the first screen.
  Instead: one small always-on agent that speaks the proprietary protocol in
  exactly one place and exposes HTTP/JSON to everything else.
- **WPF before WinUI 3**, because it is the toolchain that reliably builds.

The decisions recorded that day: apps only, on stock unmodified firmware; the
agent in Python, standard library only, so there are no dependencies to rot;
the shared layer is HTTP/JSON, not a library; iOS aims at health, alerts and
photo backup, not file browsing, which the built-in Files app already does over
SMB. Custom firmware was kept as a separate long-term fork needing a
sacrificial second unit, because BeyondRAID's on-disk format is undocumented
and a modern kernel cannot read an existing pack.

## Recovering the protocol (2026-07-24 to 26)

**07-24.** The foundation landed: the agent with a mock driver, the protocol
package, and a scaffolded Windows app. The first assumption was that reading
the Drobo required solving the client-to-device request header on TCP 5000
(capture, dissector, then driver). That was wrong in the best way: the device
volunteers a complete status document the instant anything connects, with no
login and no header. The read side was unblocked without a capture. A phantom
sixth bay was fixed the same day, and discovery was rebuilt around the
device's permanent serial rather than its address, refusing anything else
that answers on the same IP.

**07-25.** A real WiX MSI installer. Capture tooling: first a userspace tap
that needed no packet-capture driver, then genuine captures of the original
Dashboard talking to the device. Two findings reshaped the plan. First, the
Dashboard *sends nothing* to get status; it is a push model, so "poll less by
asking for pushes" was tested against the device and rejected. Second, the
sensor fields the blueprint expected (cache battery, fan, PSU, per-drive
temperature) were not obtainable the way we assumed, and were shelved past
Phase 6 rather than left as blockers. The health fields the greeting already
carried were surfaced instead. By the end of the day the command channel was
solved: TCP 5001, `DRINETTM` framing, and a 220-byte login carrying the
device serial twice. One capture of the Dashboard clicking Identify confirmed
the frame shape; sending commands ourselves and reading what came back filled
in the rest. No dissector was needed for any of it.

**07-26.** Two privilege tiers found: the health commands sit behind a
cryptographic handshake this project does not have. `eCmdGetConfig` became
the first command sent. Network and shares reached the dashboard. One file to
double-click. Getting to your files was diagnosed and fixed: Windows 11
refuses the Drobo's guest logon (`0xC05D0003`), and the answer is to connect
as a named user. Chassis temperature came back, and the reason it had ever
been "impossible" was a parsing bug in our own code. The capture dissector
was found to have read pcapng block types big-endian, so it had reported zero
frames on every capture it had ever seen; fixed. Performance statistics were
unparked the same way.

The roadmap's critical-path section was wrong twice in a row and says so,
kept on the record rather than rewritten. Two rules came out of this stretch
and never left: a guessed layout is marked as a guess, and the protocol
library must never claim to reach hardware until a capture confirms it.

## The finish line moves (2026-07-27 to 31)

**07-27.** The Windows app grew an icon, a tray, settings, and a real Connect
button; the dashboard learned to open even when the Drobo does not answer.
Then the first write: **Identify**, blinking the front lights, admitted under
a four-part test (touches no file or pack, affects no availability, is
reversible, costs nothing worse than confusion when wrong) that every later
write would have to pass. The maintainer then read the full list of what the
original Dashboard could do and said the project was not done until it
matched. Phase 1's definition moved from "show the Drobo's health" to
"match what Dashboard actually did". Share editing was built that day
deliberately *without* the ability to send. Access codes were measured
instead of guessed. The SDK was split into its own package, scoped as two
days' work and finished the same afternoon. Every safe command was sent to
the real device; Identify was sent about twenty times, and a retry that
should not have been added was taken out.

**07-28.** Volumes reached the app. "You're on the wrong Wi-Fi" replaced
"cannot reach the Drobo", because Windows can silently auto-join a different,
higher-priority network, and that had been mistaken for the Drobo being down.
Diagnostics learned to lead with *why*. A document
was written listing exactly what was blocked on a person rather than on code.
The maintainer's four decisions were recorded: the updater built in but
dormant; desktop notifications yes, every switch off by default; DroboSync
built in but dormant until a second unit exists; no built-in file browser
(reversed on 08-19). The updater and notifications shipped that day, both
quiet.

**07-29 to 31.** The last commands that answered were closed out. The question
"can the firmware stand in for the Drobo?" was answered with evidence. The
photo dedupe index became a cache rather than the truth. The capture became a
double-click. The Dashboard client itself was mined as the source that had
been under-used all along. Patents were added as a protocol source at the
maintainer's suggestion. Other people's Drobo software was checked: one
mapping adopted, two refused. `CREDITS.md` recorded what was borrowed and
only that. A file carrying the maintainer's Wi-Fi network names was scrubbed.
Two updaters became one.

## Hardening, alerts, backup (2026-08-01)

Render stopped assuming every payload was a reading; stale asynchronous work
stopped stamping itself over newer state. Settings became tabs and the title
bar went dark. Email alerts, sent by the agent rather than the Drobo. A splash
screen, and a launcher whose simulator does not outlive it. Backup to an
external drive: plan first, confirm, then robocopy with retries bounded so an
unreachable share reports a problem instead of hanging for a million tries.

## The capture pays off (2026-08-05 to 09)

The capture script learned to find a Drobo whose address had moved. On
**08-06** one capture of the original Dashboard settled three things at once:
LED brightness is written through `eCmdSetConfig`'s `<Dim>` field (Dashboard
never sends `eCmdSetDimming`); rename goes through the whole network-config
block; and the event log is fetched with `eCmdGetUserEventLog` and a path of
`/uel.log`. The command tried before, number 83 (`eCmdGetEventLogs`), had
simply been the wrong one; this record said 90 until 2026-09-26, and 90 is
`eCmdGetUserGroupList`. The same capture showed a tool "that was lying to itself", and that
Identify is really the *Blink Lights* button, which takes an interval. The
interval was sent, an A/B entry point added, a Stop button because Identify
is a mode not a pulse, and on **08-09** Identify was tested on hardware: on
this unit it does nothing, and the code had not been checking. Both the
"takes an interval" finding and the hardware test were narrower than they
looked, as 2026-09-25 showed. The interval had already been on the wire on
07-25, with a value of 900. The test sent an empty parameter and 15, never
900.

## 1.0.0 and the publishing model (2026-08-10 to 11)

**08-10.** Release 1.0.0: the MVP bar met on real hardware. A privacy scrub,
then a deeper audit that found the author's real name in the original
blueprint's Word metadata; the blueprint was converted to Markdown and the
document removed. The publishing model was decided the same day: publication
is a code-only export of the clean tree, and the working history is never
published -- never a fork and never a history push. (Amended 08-28, 09-09 and
09-21, and replaced on 2026-10-08, when this repository itself became the
public one; see that section.) Thirty-two export-gate findings were closed.
**08-11**, the separate public repository (retired 2026-10-08) went live.

## Writes, done carefully (2026-08-11 to 19)

**08-11.** The read-modify-send configuration transaction: read the device's
live settings block, change one field, echo the rest back exactly, and refuse
outright if the block holds a field this software does not recognise. LED
brightness was the first write through it, verified on hardware. Spin-down
followed. Starting and stopping a DroboApp, gated and validated against the
device's own list. A surgical share-permission write path. A write-safety
audit closed before any of it shipped. **08-12**, duplicate user rows in a
share's access list were refused instead of half-changed, and share access
was verified on hardware. **08-16**, PSU and fan alerts with an end-to-end
notification simulation; the discovery that the device coerces a bare
spin-down delay of 0, so "off" is the feature bit and the delay together; a
three-lens audit; a capture of Dashboard creating a throwaway share; and the
capture tool learned to stop early and never leave `pktmon` running.
**08-19**, the Shares tab moved first, an in-app file browser arrived
(reversing the 07-28 decision), the config-write endpoints shipped, and the
Advanced Configuration panel appeared behind an admin login that is honest
about being an in-app safety catch, since the device asks no password for
any of these writes.

## The event log, create and delete, and honesty about outcomes (2026-08-21 to 26)

**08-21.** The real user event log, fetched and shown in both the Windows app
and the browser dashboard, with the per-line format marked as not decoded
rather than invented. The browser dashboard reached write parity with the
app. A health card, an event-log viewer, per-share user lists. And a
documentation truth-up: every provably false claim corrected against the code
and the git history, a discipline that has been repeated since.

**08-26.** Creating and deleting a share, from the 08-25 capture: the whole
share configuration resent with one compact node appended, or with one node
absent plus a delete confirmation. Applying create to the real device showed
the device closes the connection instead of answering, so a write that
succeeded was reported as a failure; from then on a write the device never
acknowledges is decided by reading the device back. Every write now reports
exactly one of four outcomes (`verified`, `verified_unacknowledged`, `failed`,
`unknown`) through the agent and both UIs, with a UI state for "nobody can
say". A throwaway share created during that run was left on the device on
purpose, because the delete path had never been sent to hardware.

## Truth-up, testers, and an installer people can run (2026-08-29 to 09-02)

**08-29.** The phase status was corrected: Phase 2 had read "not started"
while most of it was built. The published roadmap became a derived document,
generated from the working one with internal procedure and personal detail
removed, so it cannot quietly drift (both derived roadmaps were since retired,
2026-10-08). **08-30**, a permission code that had
never been observed on the wire was found described as "access allowed" and
relabelled as the unknown it is. **09-01**, a PII scanner and pre-commit
guard; Office documents excluded from tracking after a `.docx` nearly
re-entered the history carrying the author's name; and the model clarified:
the public repository (then a separate repository; see 2026-10-08) is a
read-only release for **feature testers**, nobody
but the maintainer edits either repository, and every public push ships a
changelog. **09-02**, an audit hardened two soft spots (the agent's HTTP
handler now times out a stalled connection; the app gained a last-resort
exception handler) and added API tests for routes that had none. One of its
own findings was refuted by the code: an "XML escape" for the device's volume
name turned out to be unreachable, because the module already refuses a
volume carrying an XML-special character rather than guess how the device's
parser would take it. The escape was reverted and the refusal pinned in a
test so the next person does not repeat the mistake. The installer became something
a tester can run with nothing else installed: it bundles the Python runtime,
hash-pinned; the agent's config and token live in the user's profile and
survive uninstall; there is a Start Menu Uninstall entry and a testing guide
inside the install folder; and a lifecycle test installs, runs the product
through its own launcher, uninstalls, and upgrades, sixty-three checks.

## Privacy, an installer that behaves, and a login the PC remembers (2026-09-04 to 09-10)

**09-04.** Two documents were written so that "what is built" stopped having to
be re-derived from the code every time somebody asked: this one, and
[`capabilities.md`](capabilities.md). Reading the tree to write them turned up
88 stale claims across the record, corrected the same day -- three of them
introduced by the new documents themselves. **09-05**, code signing was decided
against rather than deferred; the reasoning is at the end of this file.

**09-08.** The installer became an ordinary Windows program: per-machine, into
`C:\Program Files`, with the administrator prompt that implies. Every runtime
write was traced before the move, because a program folder that is read-only
for standard users is only safe if nothing needs to write there. Settings, the
agent's `config.json` and its access token were already per-account under
`%LOCALAPPDATA%`; the only thing that had ever landed in the install folder was
CPython's `__pycache__`, which it skips silently when it cannot write. Nothing
else had to move. A per-machine package cannot upgrade a per-user one -- the
two sit in different installation contexts and Windows Installer cannot see
across them -- so anyone holding an older copy uninstalls it first, which the
changelog and the bundled testing guide both say. Everything downstream
followed in the same commit: the fallback installer, its uninstaller, the
app-finder used by the capture tooling and the lifecycle test all moved to
Program Files, HKLM and the all-users Start Menu.

The same day, the app learned to **save a share login on the PC**, in Windows'
own Credential Manager, encrypted by Windows under the signed-in account and
revocable by the owner in Control Panel. It stores nothing itself: no encrypted
blob in settings, no hand-rolled crypto, no key material this project has to
get right. It also removed a real limitation -- the tick box used to be greyed
out unless you were mapping a drive letter, because the only mechanism then was
a flag Windows applies to mapped drives and silently ignores otherwise. The
credential is written only after the connection succeeds, because a saved
password that was just rejected is a login that fails for ever with nothing to
explain it. Four code comments and four documents promised this software stored
no password; that promise was accurate until that day, and was corrected in the
same commit rather than left behind.

**09-08 to 09-09: a privacy audit**, prompted by the release being close.
Nothing it found had reached the separate public repository of the time, which
was clean;
all of it would have gone out in the next export. The compiled app carried the
build machine's identity, because the compiler records where it built: a path
naming a Windows account and a cloud-sync folder, inside the MSI, on its way to
every tester. Nothing in the source contains that string, which is why reading
the build script would never have found it -- it was found by scanning the
built file. Fixed at the compiler, and the packaging step now searches the
compiled binaries for a user-profile path and refuses to package one, which the
check it already had could not do: that check skipped `.exe`, `.dll` and
`.pdb`, the only files that carry this. It had the right rule and could not see
the file that broke it. Two real share names -- recorded from a live
capture, removed once already in August -- had come back into the test
fixtures on 08-26, and were replaced again, every case this time. And the
access token was appearing in the agent's console, because the log printed
whole request lines and the token is accepted as a URL parameter: in the very
window testers are asked to paste into a public issue.

The most useful finding was about the guards themselves. **Name detection was
entirely off**, because the pattern list it reads is per-machine and gitignored
and simply did not exist on the machine running it -- and both the scanner and
the commit hook treated a missing list as success and printed "clean". A guard
that says "clean" when it checked nothing is worse than no guard, because it is
believed. They now say so loudly, the scanner looks only at the files git
tracks, and it no longer prints the name it finds.

**09-10.** The access token stopped being accepted as a URL parameter on every
route. The two ways a request can carry it are not equally hard for someone
else's web page to produce: a custom header cannot be set by a cross-site form
at all, and a cross-site script that sets one is preflighted by a handler that
answers no preflight -- while a query string is just a URL, so a plain form on
any page the user happened to be looking at could have reached the route that
starts a backup. `/api/events` keeps the query form, because an `EventSource`
genuinely cannot send a header.

## One branch, one layout, and an app you can read (2026-09-14 to 09-17)

**09-14.** Three things the maintainer asked for. Every button and drop-down in the
app got a template of its own: without one, Windows' stock template took over
on hover, press and disable and painted light backgrounds under this app's
light text, and drop-down lists opened white with near-white items -- the
drive-letter list the maintainer could not read. Measured rather than asserted: an
offscreen harness loads the styles as written, renders every button style
normal, hovered, pressed, focused and disabled, plus the combo box and the real
Connect dialog, and reads the contrast off the pixels. All 32 states are at or
above 4.5 to 1; the app's blue and its delete red are each a shade darker,
because white on the old ones measured 4.4 and 3.7. Settings gained a versions
card, naming this app's version and the connected Drobo's firmware. And the
simulator gained an account -- user `admin`, password `admin` -- because with
no accounts at all, "Connect as..." was greyed out and the sign-in flow could
not be tried without hardware. The simulator checks that login itself; nothing
simulated is ever handed to Windows, where a pretend login could shadow a real
one.

**09-15.** The long-running feature branch landed: Phase 1's foundation merged
into `main`, with the privacy guard reconciled across both. The simulator's
launcher was fixed -- on a PC with Microsoft's Python install manager it had
been starting a Store alias that cannot run hidden, and then reporting that the
agent had not started -- and the agent stopped printing its access token in its
opening line. The request log had been fixed on 09-08; this one line was
missed, which is the ordinary shape of a leak: the general case is fixed and
one specific caller keeps doing it.

**09-16.** The repository was reorganised: twenty renames and no content
changes, so git keeps every file's history. The ten agent suites moved beside
the SDK's into `agent/tests/`, finished documents moved into `docs/archive/`,
and the published-release files moved together into `docs/public-export/`
(that folder was removed on 2026-10-08, when this repository became the public
one).
Every path those moves broke was fixed in the commit after, and every folder
gained a README saying what is in it, so the repository explains itself when
browsed. The same day the **fallback installer's uninstaller** was brought back
into step with the installer that had gone per-machine on 09-08: the same four
locations, character for character, checked by parsing both scripts rather than
by reading them; administrator rights asked for at most once instead of
looping; and nothing removed at all while the app or its agent is still
running, which it had previously done by halves -- taking the shortcuts and the
Add/Remove Programs entry, leaving the program folder, and reporting success,
so there was nothing left to uninstall from. **09-17** finished the job: the
helper that deletes the program folder after the script exits now retries for
about twenty seconds instead of trying once, half a second later, with the
error silenced; the final message waits to be read instead of vanishing with
the window; and `Check-ForUpdates.bat` reads the settings of the copy it is run
from rather than another install's.

Also on 09-16, a fix made in the published copy was carried back here: **the
frame reads a peer on the network could hold open**. A socket timeout bounds
each read attempt, not the whole read, so anything answering on the status port
could declare a large payload, trickle one byte inside every window, and hold
that read open for hours -- and the agent holds the process-wide discovery lock
across it, so one such peer was enough to wedge the device picker until a
restart. The reads now take a deadline covering header and payload together. It
then sat unmerged until 09-19.

## Unblocking, in parallel (2026-09-19)

What was left after all of that was a set of items that needed nobody's
decision, only somebody to do them. They were done on a branch each, merged
together, and the record was then made to agree with the code -- which is what
this section is.

The bounded-read fix from 09-16 was **merged at last**, and the identical
defect on the command channel was closed the same way -- but only for the
settings reads and writes, whose `config_cmd` reads pass the new deadline.
Every other command-channel request (temperature, the event log, Identify,
DroboApps, DroboSync) still calls the same read without one. This record
said the command channel was closed until 2026-09-26. The simulator's launcher stopped writing
**one fixed access token** into the settings it generates: the launcher is a
published file, so that value was readable by anyone, which made it the same as
no token at all -- and one token opens every route on the agent, including the
one that starts a backup. It now leaves the token empty and lets the agent mint
a random one, exactly as it already did for a real install, and a PC still
carrying the old constant is moved onto a fresh one at the next launch.

**The tests are set to run by themselves.** All thirty Python suites and the
Windows app's own checks are wired to run on every push, on Windows, beside the
personal-data scan and the compile check that already ran; a suite that runs
but checks nothing, or that moves out of its folder, turns the run red rather
than quietly dropping out of the count. For a day it was written and read but
not watched, and both records said so rather than counting an unrun workflow
as working; the first push carrying it ran green on 2026-09-20, both jobs,
including the Windows app job on a hosted runner. That matters because four fixes to the
fallback uninstaller had sat unexercised since 09-16. Those two scripts now
have a harness of their own -- **78 checks** driving a real install, a
re-install over it, the refusal while something is running, and an uninstall
that removes all four places and keeps the user-data folder -- into a
temporary folder and a scratch registry key, so it needs no administrator
window and touches nothing real. What remains unproven for them is one
elevated install into Program Files.

Making those four places parameters opened a gap of its own, closed the same
week: passing one of them is what tells both scripts "you are not aiming at the
machine", and that is also what switches the administrator check off -- so a
run given one parameter had the other three still pointing at the real Start
Menu, the real Public Desktop and the real HKLM key, with nothing left guarding
them. Demonstrated rather than argued, against stand-ins for those three
places: the uninstaller given only `-InstallDir` removed all three and exited
0. Both scripts now take all four together or refuse, and the harness runs that
refusal before it installs anything.

**The per-machine installer was finally proven, and the record of it went
wrong twice in one day.** The lifecycle test did run: on 2026-09-18, from an
elevated shell, against an MSI built from this tree, 53 checks all passing --
install into Program Files, launch of the installed copy through its own
launcher against the simulated driver, removal, and the no-desktop-shortcut
opt-out cycle. It ran on a separate, invisible Windows desktop, so no window
appeared on the maintainer's screen while they were working. Then a review pass,
finding no lifecycle logs in the working checkout and no recently built
installer there, concluded the run had been invented and withdrew it from all
three records. Both halves are worth keeping. The run was real; the logs were
absent because it was driven from a throwaway clone, which is also where the
installer it tested was built, and nothing said so. A run nobody can point at
afterwards is hard to tell from a run that never happened, which is the whole
reason this project writes down what was executed rather than what was
intended. What remained genuinely unproven was narrower: the upgrade leg,
which needs a second, higher-numbered build. **09-21**, it ran -- the same
elevated, invisible-desktop arrangement, this time with a 1.1.1 build handed
to the test as the upgrade: 64 checks, all passing, the planted user settings
surviving both the in-place upgrade and the uninstall after it. The log names
the clone it ran from.

Two guards were sharpened. The privacy scanner learned the **shape of a Drobo
serial number and a Drobo MAC address** -- the two identifiers that matter most
about the hardware, and the one category it did not look for -- and it masks
what it finds instead of reprinting it. The tool that derives the published roadmap now
runs its privacy checks **before** it writes: it used to write the file and
then check it, so a failing run left an unsafe roadmap on disk with only the
exit code to say so, while two documents promised it refused to write anything
in that case.

And the release number: `-Version` changed the package, the staging folder and
`version.txt` but never reached the compiled program, so a release built that
way would have reported a version its own Add/Remove Programs entry disagreed
with. That file version is also what Windows Installer compares when deciding
whether to replace a file, so the upgrade leg of the lifecycle test would have
been proving an upgrade whose payload still declared the old version. A build
with no `-Version` is byte-for-byte what it was, proved by hashing both. One
new check compares the seven places the release number is written and fails if
any of them disagree; it does not know what the number should be, which is
still the maintainer's to choose.

## Decisions, and a release that builds on the last (2026-09-21)

The maintainer answered the questions the 09-19 work had left open, and the record
was brought into one shape.

**The roadmap took a fixed shape** -- what this is, where it stands, what is
being worked on, the features, the future features, and how the file is kept
current -- above the detailed record, which is unchanged. It had been written
against the tree of 09-18 and landed after the 09-19 work, so the two were
reconciled line by line, and where they disagreed the code decided. Three of
its claims had stopped being true in the day between: the bounded-read fix was
no longer waiting on a side branch, the fallback installer was no longer
untested, and saving a share login had been covered from the day it was written
by a full save, replace, find and forget round trip against the real Credential
Manager, not by eight checks on a helper.

**The next release is 1.1.0**, a minor bump: every place the number is written
says so, and the changelog's top section is headed as 1.1.0, not yet released.
It now opens with the one thing a tester has to do first -- uninstall 1.0.0 --
because 1.1.0 installs for the whole machine and Windows Installer cannot
upgrade a per-user copy in place.

**How a release reaches testers changed.** (Superseded on 2026-10-08; see
"This repository goes public".) The plan for every release had been
the plan for the first: a fresh copy of the code as the first commit of a new
repository. Repeated, that would have replaced the published history each time,
and that history had grown changes of its own since August, some of them fixes
made there first, which a replacement would have quietly thrown away. From
1.1.0 each release was still to be a fresh copy of the code carrying none of
the working history, but it landed as one new commit on top of the published
history and was pushed normally. That needed a step the old way never did:
before each release, every change made on the published side since the last
one was checked against the working copy and brought across if it was missing,
because the new commit replaced every file with the working copy's version.
The first such check, for 1.1.0, went through the eight changes made on the
published side since 1.0.0 and found every fix already in the working copy --
most of them earlier, and some taken further -- so nothing had to be brought
across. It did find one fix with no check behind it on either side, the one
that keeps the access token out of the agent's console log, and that check now
exists. A trial release, made into a throwaway copy and never pushed, then
caught one way the new step could still undo a published fix without any
file's content changing: re-adding every file on Windows would have taken the
executable bit off the commit hook, which the published side had set on
purpose, so the step was made to keep file modes. The privacy sweep inside the
export was also made honest: the scanner could not look for names there, since
the list of names is private and never travels, so the sweep said it skipped
names, and names were checked instead on the working copy at the exact commit
being exported. The roadmap that shipped then was the testers' copy, a derived
document since deleted.

**Managing the Drobo's user accounts is wanted.** It had sat at the bottom of
the future list, because nothing had ever been seen on the wire -- the unit
this was built against has never reported a user -- and this project does not
guess a write. That has not changed, so it cannot be built yet; what changed is
that the capture it waits on is now asked for. The recording script gained the
steps: create a throwaway user in the original Dashboard and give it a
permission other than read/write on a throwaway share, which also pins one more
permission word to its number; then, in a second short recording made only
after the permission codes are read back, delete that user again. Everything
else that only the maintainer's own unit can settle -- share create's re-run, share
delete's first send, Blink Lights, the event log's replies -- was gathered
into the same sitting and written out step by step, in order, for the maintainer,
where until now it had been scattered across the roadmap and the script's
comments.

## A capture that had been there all along (2026-09-25 to 26)

On **09-25** the private capture from 07-25 was read again, offline. It is
the capture the command protocol was recovered from. It holds exactly one
press of the original Dashboard's Blink Lights: command 26 with
`<IdentifyInterval>900</IdentifyInterval>`, answered with the accept code,
and no second press to turn it off. The protocol map's command table had
recorded that parameter on 07-25. The first write, on 07-27,
sent Identify with an empty parameter block, and after that nobody went
back to the table. From 08-06 onwards the parameter was "found" in the
original Dashboard's binary. The records then said that nobody had ever seen
the button pressed, that the Dashboard's value was fifteen of something, and
that one capture would settle it. The project's own default became 15, and
the 08-07 hardware test sent an empty parameter and 15. Nothing lit, but
that says nothing about 900, which has never been sent. The decisive test
needs no capture: send 900 while watching the unit. The maintainer's hardware
session now ends with it. The default stays at 15 until then, because a
different value on the wire is a code change and waits on the test.

**09-26**, the records were corrected against the code in one pass,
documentation only. The same adversarial check that re-read the capture had
turned up seven more claims the code did not support:

- The bounded frame reads cover the status channel and the settings reads
  and writes, not "both channels": five other command-channel requests still
  read without a whole-frame deadline. That is now written down as open work.
- The event-log command tried before the right one was 83, not 90.
- DroboApp start and stop was graded Complete with no record of this
  project's sender ever reaching a device. It is now Built, unverified.
- The roadmap's Complete list held Identify and the saved share login, which
  its own Features table grades lower. Its "Parked" row still listed the event
  log, unparked on 08-21. It said this work still had to land on `main`,
  where it already was.
- The Windows app's admin sign-in was described as the device's own. It is a
  safety catch inside the app.
- Two records said the software admits that nothing visibly changes on
  Identify. It says the opposite: the lights "should be blinking now".
- Share access code 0 was called confirmed in the Windows app's and the
  browser dashboard's help text, in two comments and in the capture script.
  Only code 1 is confirmed; the help text now says so.

The working notes in `CLAUDE.md` had drifted the same way. They gave the
installer test's count as 63, where the recorded runs were 53 and 64. They
called Phase 1 complete, put the event log on the wrong tab and DroboApps
inside the admin card, carried a stale note about the build environment,
and said share create and delete were not shipped, although both are listed
as Added for 1.1.0. One disagreement was flagged rather than settled: backup
is Working in the roadmap and Built, unverified in `capabilities.md`, a split
left by the 09-21 merge. That is the maintainer's call.

## A shortcut nobody had run (2026-09-26)

The Start Menu's **Check for Updates** shortcut had been broken in every
installed copy since 09-02, the day the installer began bundling its own
Python, and nothing noticed for more than three weeks. It ran
`python -m drobo_agent.updates` from inside the agent's folder. With the PC's
own Python that works, because the folder a module is run from is on the
import path; the embeddable runtime the installer bundles ships a
`python314._pth` that runs the interpreter isolated and leaves that folder off
the path, so every run stopped at `No module named 'drobo_agent'`. The script
reacted only to "Python not found", and the `pause` that would have held its
window open had been removed in the same 09-02 commit, so the window closed at
once and whoever clicked it saw nothing at all. `run_agent.py` never had the
problem, because it puts its own folder on the path first.

Three records said it worked. The lifecycle test checked that the shortcut
existed and never ran it. The 09-17 fix to the same script made it work inside
the source tree, where the PC's own Python runs it, and the changelog entry for
that fix said an installed copy "was never affected" -- true of that fault, and
read as true of the script. The lifecycle test's own opening line is that an
installer nobody has ever run is just a file; the same was true of this
shortcut.

The fix follows `run_agent.py`: a small `agent/check_updates.py`, run by its
path, puts its own folder on the path and hands over to the same update code.
The window now waits for a key whatever the answer. A check that cannot run
exits with a status of its own, 3, because Python's status for a crash is 1,
which is the check's word for "an update is available". It was proven against
a copy of the staged runtime -- the answer, a broken settings file, a missing
package, no Python at all, and the source-tree fallback. `agent/tests/test_updates.py`
now runs it on an ordinary interpreter isolated the same way, after first
showing that the old invocation fails under those flags, so the check cannot
pass on an interpreter that never had the fault. The lifecycle test now runs
the installed shortcut's own target and requires the "switched off" answer and
exit status 0; run against the old script, those checks fail. They have not yet
run in a real elevated install.

## An agent that starts before its Drobo (2026-09-26)

A monitor that refuses to run while the thing it monitors is missing had been
fixed once already, on 2026-07-27: an agent that could not reach the Drobo at
start-up used to crash, and from then on it started anyway and raised the
"cannot reach" alert. That fix kept one failure fatal on purpose. When
automatic discovery found nothing, the driver reported it as
`ProtocolNotMappedError` -- a leftover from before the protocol was mapped --
and the monitor exited on it as "a programming/config error, not a network
condition". On that day it was right: the default driver was the simulator,
and the only way to reach that line was to have asked for the real driver by
hand.

On 2026-08-10 the default became the real driver with automatic discovery,
and that quietly changed what the line meant. From then on "discovery found
nothing" was mostly a network condition -- the Drobo off or still booting, or
the PC's own network not up yet -- and the agent still exited on it, before it
served anything. The start-up method `running-the-agent.md` recommends is a
Task Scheduler job at boot, with no window and no restart, so a PC that booted
before its Drobo ran no monitoring at all, and nothing said so. Two later
pieces were written around the exit as if it were intended: the installer's
agent window, whose footer named "no Drobo found" as the usual reason it had
stopped, and the installer test, which gave the exit as its reason for
launching against the simulator.

It was found by reading the code, not by a failed boot. Discovery finding
nothing now has its own type, `DroboNotFound` (since renamed
`DroboNotFoundError`), and gets the answer an unreachable Drobo already
got: the agent starts, serves the dashboard, raises
the critical alert, and runs discovery again on every poll until the Drobo
answers. The unused `ProtocolNotMappedError` is gone. A device that answers
and is provably not this Drobo still stops the agent -- which one it is, a
replacement or an impostor, is the user's call -- but with a message naming
both serials rather than the traceback it used to end in; a reply that is not
a Drobo's, at a fixed address, now starts anyway like any other unreachable
device. The tests start the real driver in its shipping configuration with
discovery made to find nothing, and follow it until the Drobo turns up. A real
boot before the Drobo has not been tried.

## This repository goes public (2026-10-08)

**10-08.** The maintainer decided to publish this repository itself and to
retire the separate public copy. Since 08-10 every release had been a stripped,
code-only export of this tree into a second repository, and keeping the two
apart had grown machinery of its own: a testers' roadmap and a public one,
both derived from the working roadmap by a script with its own privacy gate;
an export procedure of eight steps; and, from 09-21, a carry-back check before
every release, because each export replaced the files on the published side.
Now there is one repository, and everything in it is published, documents
included.

**What is not published is its history.** The public repository starts from
one fresh commit of the scrubbed tree, and the earlier history is kept
privately, as the record of how the protocol was recovered. **The commit hashes
quoted in this document and the others point into that private history and
will not resolve in the public one.** They are kept because they are how a
claim gets checked against the code, and the dated text around each says what
it changed.

Every tracked file was scrubbed first, the same day, applying the decisions the
derived-roadmap script had already recorded to the files themselves: no real
person other than the published credits, no contact details, nothing that
identifies the maintainer's unit (its share names, or which drives its array
holds and how full it is), the maintainer's PC and files, or the maintainer's
circumstances, and "owner" written as "maintainer" wherever it meant a person.
Fixtures recorded from the real device now carry synthetic values in the same
format, with the tests updated to agree. Then the machinery went: the export
procedure, the carry-back check, the derived roadmaps and the script that made
them. Nothing went with the separate repository: the 09-21 comparison of its
eight commits since 1.0.0 had found every fix already here. 1.1.0 is the first
release from this repository, on its own Releases page; the advice to
uninstall 1.0.0 before installing it is unchanged.

The same day the Windows app moved from .NET 8 to .NET 10. The installer
carries its runtime inside it, and .NET 8's support ends on 11-10, so 1.1.0
would have handed testers a runtime a month from its last security fix. The
move was two target-framework lines, the SDK the build script insists on, and
the version the CI workflow installs; the app and its tests built and passed
unchanged on .NET 10 before anything was committed, in a scratch copy, then
again in the tree.

## 1.1.0 goes out (2026-10-08 to 15)

**10-08.** With the .NET 8 and 10 SDKs installed on this PC, the 1.1.0 MSI was built on
.NET 10 (68 MB, the build's own identifier check clean) together with a throwaway 1.1.1
for the upgrade leg, and the maintainer ran the lifecycle test from an administrator
shell: 70 checks, all passing, including the installed Check for Updates shortcut that
had been broken in every installed copy until 09-26. Part-way through, an error
mentioning a constructor appeared in that console. It failed no check, it was not
captured, and it could not be reproduced: every install, uninstall and upgrade logged
success, and the installed app opened normally both alone and against the simulated
driver. It is recorded here rather than explained; if it shows again, its text is what
is needed.

**10-15.** 1.1.0 was published on this repository's Releases page, with the MSI's
SHA-256 beside it.

## A licence (2026-10-19)

**10-19.** The maintainer licensed the project GPL-3.0-or-later. The question
had been open since 09-04, when a LICENSE file was deferred with GPL-3.0 and
PolyForm Noncommercial as the two candidates, and until now everything here
was all rights reserved: readable, because the repository is public, but not
granted to anyone. `LICENSE` at the top of the repository is the GPL's text,
verbatim. The README's License section says so in plain words. The SDK's
`pyproject.toml`, which had carried a deliberate "Proprietary" placeholder
since its MIT line was removed, now declares GPL-3.0-or-later, in the table
form the pinned setuptools floor accepts, with the matching classifier. The
installer build copies `LICENSE` into the program folder beside `TESTING.md`;
the bundled Python already ships its own `LICENSE.txt`, because the build
unpacks the embeddable zip whole. That one-line build change has not been
through a Windows build yet.

## Kept figures say how old they are (2026-10-19)

**10-19.** The rule recorded when the command port first stopped answering, on
07-26, is that a kept reading travels with its timestamp and anything showing
it shows its age. The temperature follows it. The read/write counters, fetched
on the same connection, did not: the driver noted when they arrived in
`_perf_at` and nothing read it, so after a failed fetch the last figures were
served every poll as a fresh measurement. A review of code that nothing used
found it, and it was wired up rather than deleted. The performance data now
carries `measured_at`, and the browser dashboard and the Windows app show
"Last read ... ago" once the figures are more than three minutes old, one
missed two-minute refresh plus slack. The browser note that said the figures
were "refreshed every couple of minutes" now says the agent asks for them that
often, which stays true while the Drobo is not answering. The Windows half has
not been through a Windows build yet.

## A redactor that redacts (2026-10-19)

**10-19.** `tools/dashboard_strings.py`, the tool that found `DimmingLevel` and
the dead API Gateway in the original Dashboard, said it redacted credentials
on sight, and `command-surface.md` repeated it. Half of that was never
written. Its `_KEY_CONTEXT` pattern, the field names that introduce a key, was
defined and nothing used it, so `x-api-key: ...` and `?api_key=...` were
printed in clear; and the test that spared tag-shaped strings looked only at
the first twenty characters, so a key that began with a letter passed too. The
same review of unused code that found the performance timestamp found this,
and again it was wired up rather than deleted. The first wiring proposed would
have matched the `://` of every URL and hidden the very endpoints the tool
exists to show, so the field name now has to be followed directly by `:` or
`=`. Of the two stricter tag rules on offer, the maintainer chose the stricter:
only an all-letter string is spared. A new suite, the fifth under `tools/`,
checks both with made-up strings; eleven of its 25 checks fail against 1.1.0.

## Names that say what they mean (2026-10-19 to 24)

**10-19.** The maintainer approved a pass that brings the Python in line with
PEP 8: every name says what it holds or does, every exception's name ends in
`Error`, and, once the renames are done, lines are rewrapped to 79 columns (72
for comments and docstrings). It goes in small steps, renames first, and each
step is held to 1.1.0 by the same suites with the same counts, a recording of
every byte the SDK sends to a fake Drobo on loopback, and fingerprints of the
browser dashboard and the pairing QR code. Public names are renamed too,
without compatibility aliases: the agent already relies on a renamed SDK type
failing at import rather than being quietly mis-filed, and an alias would undo
that. The first step renamed the exceptions. `ShareVerifyUnavailable` became
`ShareWriteOutcomeUnknownError` rather than just gaining the suffix, because
what it reports is a write whose outcome is unknown, and two of its three
routes are a translation of `ConfigWriteOutcomeUnknownError`; an internal
signal that is not an error at all became `_NoChangeNeeded`.

The public and cross-module names followed in five more serial steps
(framing and the write path, shares and the other SDK writers, status and
discovery, the agent's names other modules reach, and the conventions such
as importing `ElementTree` under its own name), then the names local to each
area in eight units: the SDK's write path and read side, its tests, the
agent's core, its API, its tests, the tools and the repository's own
scripts. The layout pass came last, area by area, mostly two commits each:
one that only re-wraps code, proved by an unchanged syntax tree, and one that
only re-flows comments and docstrings, proved by an unchanged tree once
docstring whitespace is ignored and an unchanged sequence of comment words.
The last SDK unit and the seven-tool unit did both in one commit, proved the
second way, and several areas needed a third, follow-up commit. The style
checker's findings went from 6,273 to 0, and a `.flake8` file now holds the
limits so a plain `flake8` run checks the same thing.

Three units were held back by their reviews and landed only after the final
review found the refactor short of done: the tools' renames, and the layout
of the SDK's read side and of seven tools. By then the tools had been laid
out without their renames, so the renames were carried onto the laid-out
files token by token, and each result was proved equal, by the same two
syntax-tree and comment-word checks, to the reviewed rename. The final
review also caught a changelog list that left out many agent and script
renames it promised to list, and an entry claiming no visible text had
changed when ten programs' `--help` descriptions had been re-wrapped; and
bringing in the held-back layout showed a reflow slip that had joined some
docstrings' closing quotes to their last sentence. Through all of it
nothing sent to a Drobo changed: the same suites with the same counts, and
the same 239 frames from the 74 recorded calls on loopback, at every step.
A last tidy-up fixed what the greedy reflow had left behind: `#:` blocks
broken into alternating fragments, two-line comments and docstring
paragraphs whose second line was one word, strings split with a lone word
on a line, and section rules split in two.

**10-23.** The last step made the result stick. A `.flake8` file nobody runs
is advice, so a new workflow, `style.yml`, runs flake8 with those settings on
every push. It is a workflow of its own rather than a step in `lint.yml`,
because `lint.yml` installs nothing on purpose, so that its red always means
broken code; flake8 has to come from PyPI. Every version is pinned too,
flake8 and each of its plugins, so an upstream release cannot turn the job
red on a commit that changed nothing. Run with those exact versions
from the top of the repository, it reported nothing.

**10-24.** An independent audit of the finished pass found a few things it had
left. Three `# noqa` comments hid nothing at all, two more kept an import
nothing used and a line of fourteen imports, and one hid a 115-column line that
splits like any other, which also made the changelog's claim that the last such
lines had been dealt with untrue. The base driver class still kept the Drobo
settings as `cfg`, an abbreviation the naming rules ban, though nothing read
it: the same dead copy an earlier review had removed from the photo store. Two
names in tests still read as shorthand, `tmp_dir` and `cs_category_ids`. All
were fixed, still held to the same suites, counts and recorded bytes.

## 1.2.0 is next (2026-10-29)

**10-29.** The maintainer had chosen 1.2 for everything landed since 1.1.0,
on one condition: that the PEP 8 refactor was finished first. The audit of
10-24 confirmed it was, once the gaps it found were fixed. **The next release
is 1.2.0**, a minor bump: every place the number is written says so, and the
changelog's top section is headed as 1.2.0, not yet released. It opens with
what a tester needs before installing: 1.1.0 is upgraded in place and keeps
its settings, 1.0.0 must still be uninstalled first, and a script of a
tester's own that imports the SDK breaks on the renamed names, whose full
list is under Changed. Unlike 1.1.0, whose number was bumped weeks before its
MSI was built, this release changes the binary itself, because the number is
compiled in, so the MSI has to be built from a tree that already says 1.2.0.
The release is therefore cut in two commits: this one, and a later one that
dates the changelog and records the elevated lifecycle run, once the MSI has
been built and tested on Windows.

## The principles that emerged

None of these were written down first. Each was learned.

- **Never guess a write.** A guessed parameter is an unreviewed instruction to
  somebody's only copy of their data. Every write format came from a capture
  of the original Dashboard, and a write is called working only once it has
  been verified on hardware. Identify's interval is the one deliberate
  exception: the project sends its own guess, 15, where the capture shows
  900, until 900 has been tried.
- **Fail safe on the unknown.** A status code the parser does not recognise
  reads as a warning. An event-log line whose format is uncaptured carries an
  explicit not-decoded marker rather than a fabricated timestamp.
- **Report what actually happened.** Verified; verified but unacknowledged;
  failed; unknown. Never a guess dressed as a result.
- **Keep the wrong turns on the record.** The critical path that was wrong
  twice, the Identify that does nothing (at 15; 900 is untried), the
  dissector that never read a frame, the capture that was forgotten and then
  asked for again: rewriting them would lose what they teach.
- **Privacy is an engineering practice.** Serials, network names, real names
  and captures never enter the tree, and what is published is a fresh copy of
  the tree, never the working history.
- **Documents are truth records.** They are corrected against the code and
  the git log, dated, and say when they were wrong.

## Where the path leads next

What is built, what is in progress, and what is planned is kept current in
[`capabilities.md`](capabilities.md). The open items, in the order they are
worth doing: build 1.2.0's MSI on Windows, run its elevated lifecycle test
(which also runs the Check for Updates shortcut) and publish it, which is what
puts everything since 1.1.0 into testers' hands; re-run share create on
hardware since the acknowledgement fix; send share delete to a device for the first time; send Identify at 900 while
watching the unit; start and stop a DroboApp on hardware for the first time;
give the remaining command-channel reads the whole-frame deadline the
settings reads already have; one elevated install of the
fallback scripts into the real Program Files;
record the original Dashboard creating and deleting a user, which user
management now waits on; and then the phases that need what this project does not have: a Mac for iOS, a second unit for DroboSync, a
redundant pack to degrade for the status codes. The licence is no longer
open: it was settled as GPL-3.0-or-later on 2026-10-19.

One question was closed rather than answered on 2026-09-05: the software will
not be code-signed, because this is not a for-profit project. That is a
deliberate trade — an unsigned installer warns on first run, and a machine with
Smart App Control switched on will not run the Windows app at all until its
owner switches SAC off. The browser dashboard, served by a Python runtime that
carries the Python Software Foundation's own signature, is unaffected.
