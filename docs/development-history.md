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
  Identify. It says the opposite: the lights "should be blinking now"
  (changed on 11-20, by decision D4; see below).
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
unpacks the embeddable zip whole. That one-line build change had not been
through a Windows build yet; it went through one on 11-05 (see below).

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
often, which stays true while the Drobo is not answering. The Windows half had
not been through a Windows build yet; CI's Windows job compiles it on every
push, and on 11-05 it went into the 1.2.0 MSI (see below).

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

## Backup's grade settled (2026-10-29)

**10-29.** Backup had been Working in the roadmap and Built, unverified in
`capabilities.md` since the 09-21 merge, and the split was flagged on 09-26
for the maintainer. The maintainer asked for it to be settled from the
evidence, using the two files' own status words rather than a new one. It is
now **Built, unverified** in both.

The case for Working was real. Nothing in `backup.py` speaks the Drobo's
protocol: it plans, then runs robocopy between two paths on a separate,
explicit call. Its suite copies real files between ordinary folders, checks
that they arrived and that a second run copies nothing, and that part, which
skips anywhere but Windows, does run on every push, because `tests.yml` runs
the suites on a Windows runner precisely so that it cannot be skipped (44
checks there). But the definitions decide it. `capabilities.md` defines
*Complete* as working against a real Drobo 5N, and the roadmap's *Working*
covers *Complete* and nothing else. The source backup is built for is an SMB
share on the Drobo -- its own notes name the 2013 NAS as the reason it uses
robocopy -- and no record anywhere in this repository shows a copy ever made
from one, let alone a whole array. That is the same test that moved
DroboApp start and stop down on 09-26: no record of it reaching a device.
One copy of a real share to an external drive, from the Tools tab, moves it.

## Identify stops crediting the original with 15 (2026-10-29)

**10-29.** The agent's reply to a blink, which the Windows app and the
browser dashboard both show, said the original Dashboard "used this value"
and blinked for 15 minutes. The value was whatever had just been sent, 15 by
default, and the one captured press of the original's Blink Lights
(2026-07-25) carried `IdentifyInterval` 900. The wrong claim had been on the
record since 09-26 as a separate decision; the maintainer asked for the
wording to be fixed and the default kept. The reply now names the interval it
sent and says the original Dashboard sent 900, and the 15 minutes, there and
in both apps' help text, is attributed to the original Dashboard's own
warning, which is where it comes from. The default stays at 15, and the bytes
sent are unchanged: 900 is sent only once someone is watching the unit, and
the default moves only if the lights blink. The reply's "should be blinking
now", which nobody has seen on this unit, is left for its own decision
(taken on 11-20 as D4: the wording now says what was seen; see
"Readiness for outside testers").

## Code written to be read (2026-10-29)

**10-29.** The PEP 8 pass had given every name a proper form; it had not
made the code easy to check. The people who read this code are checking it
against a device they cannot replace, so the maintainer wrote down the rules
the code should follow (the readability section of `CLAUDE.md`) and asked
for a pass that applies them without changing anything the software does:
names that carry their unit (`timeout_seconds`, `size_bytes`), guard clauses
in place of nested branches, at most two levels of nesting inside a
function, functions of about 40 lines split into helpers whose names say
what each part does, and docstrings that say why.

It went in as 80 commits after the one that wrote the rules down. Six shared
renames came first, because a name that crosses files has to move everywhere
at once: the SDK's write-path parameters, the event log's request builder,
the agent names other modules reach, the tools' entry points, and two sets
of names in the Windows app. Then each area went module by module: the SDK's
protocol core, its write features and its read side, the agent's driver, its
API and the rest of the agent, two groups of tools, the repository's scripts
and the icon generator, and the Windows app in two halves
(`MainWindow.xaml.cs` took six commits on its own, so each stayed small
enough to review). In the SDK's write path the rule was to split, never
reorder: every `except` clause, its order, and the read-back that decides an
unacknowledged write are where they were, now inside named helpers.

Some names were deliberately not touched, because something outside the code
reads them: routes, JSON and config keys, command-line flags, environment
variables, file names, the bytes and XML sent to a Drobo, and the members of
the write gates. Neither were the private SDK names that tests and `api.py`
reach into (apart from `eventlog._command_xml`, renamed with its test in the
shared renames), `set_spindown`'s `delay` (the device's unit for it is still
unconfirmed, so a unit in the name would be a guess), or any XAML binding
path in the Windows app, since a missed binding fails silently and Windows
CI is the only build. The only `x:Name` renames, `Sub` -> `SubtitleText`
and `Remember` -> `RememberCheck` in three dialogs, moved with their
code-behind; the compiler checks those names at every use, and no binding
refers to them. A few modules were left as they were
because there was nothing to improve or nothing that could be touched: the
two package `__init__.py` files (the agent's `__version__` must stay a plain
literal for `scripts/check_versions.py`), `webui.py` (its only content
besides the docstring is the page, which is fingerprinted), the WPF
template's `AssemblyInfo.cs`, `VolumeRow.cs` (already clear, every property
a binding target), and the Windows test project, whose two compiled files
kept the names the tests use. A handful of methods that came out at 42 to 44
lines were not split further.

Nothing the software does was allowed to change, and each commit was held to
that by the same gate: all 31 Python suites with the same pass, fail and
skip counts and exit codes; a recording of every byte the SDK sends to a
simulated Drobo on loopback, 239 frames from 74 calls, byte-identical; the
fingerprints of the browser dashboard's page, the pairing QR code's SVG and
matrices, the API's notes and the robocopy command; the version check and
the privacy scan; the status, content type and JSON key set the agent's
29 read-only routes answer with, over 37 requests including their query
variants, run against the simulated driver with and without simulated settings
and with no network at all; and the `--help` output of all 14 command-line
programs and their 9 sub-commands. flake8 stayed at zero findings. Several
modules were also run side by side, old code against new, over the same
inputs. No C# compiler was available where the pass was made, so each C#
commit was checked by a syntax parse and by comparing the identifiers each
member uses before and after; the Windows CI build is still the only real
compile of the app.

Docstrings that were wrong were corrected where that was the readability
work itself: Identify's duplicated "Raises" paragraph, a helper that said it
left retries to its caller in a module that does not retry, discovery's
claim that the agent never writes to the device (untrue since 2026-07-27),
and the stray spaces an earlier rewrap had left inside words and addresses
(`127. 0.0.1` among them). Everything else found on the way was **recorded,
not fixed**, because fixing it would change behaviour, and each fix is a
decision of its own. The faults that change what someone sees or gets are
listed by area in the roadmap under Phase 6; the rest were docstrings that
disagree with their code, dead checks and unused names (`sysinfo.get_sysinfo`,
called nowhere in the repository, among them). The ones that matter
most to someone using the software: the agent clears every active alert as
"Resolved" when the Drobo drops off the network and fires them again when it
comes back, so a network blip tells the user a failed drive was resolved
(fixed 2026-11-02); a
truncated performance reply throws away the temperature and uptime already
read (fixed 2026-11-02); one malformed mDNS reply aborts the whole search instead of falling
back to the other ways of finding the Drobo (fixed 2026-11-02); a request body that is valid
JSON but not an object makes several agent routes drop the connection
without an answer (fixed 2026-11-02); the poller never checks that the Drobo it reads is still
the one it found, so a reused address would be trusted (fixed
2026-11-02); every event-log
entry is served with a 1970 timestamp beside its not-decoded marker (fixed
2026-11-02); and in
the Windows app "Change Drobo" cancels its own search and leaves the picker
stuck on "Searching…" (fixed 2026-11-02). For the maintainer to decide:
`eCmdGetNASUsernamePassword`, which by its name returns credentials, is not
in `DO_NOT_SEND`, and the credential redaction misses tags such as `ApiKey`.

The maintainer settled both on 2026-11-02, when the recorded faults were
fixed one commit each: the redaction now catches CamelCase and snake_case
key names, and `eCmdGetNASUsernamePassword` joined `DO_NOT_SEND` in a group
of its own, credential reads, since it is not a write. It had been sent
once, by the second sweep on 2026-07-27, and answered empty; that sweep's
list drops it, or the sweep's own guard would now refuse to run. The same
day the gate began counting any id or name the table does not hold as
refused, and its integrity checks stopped being `assert`s that `python -O`
removes. The refused count went from 53 names to 54.

## The installer, tested off the maintainer's PC (2026-11-05)

**11-05.** Until now the installer's lifecycle test had run only by hand,
from an administrator shell on the maintainer's PC: 53 checks on 09-18, 64
on 09-21, 70 on 10-08. Anything that changed the payload afterwards rested
on the build succeeding, and the licence's line in the build, added on
10-19, had not been through one. GitHub's Windows runners run as an
administrator and are thrown away after each run, which is the machine the
test asks for, so `.github/workflows/installer.yml` now builds the MSI
there with WiX 5.0.2, builds a throwaway copy one patch number higher, and
runs the test with both. The test gained one check, that `LICENSE` reaches
the install folder. The same workflow publishes a release: on a `v*` tag
that matches the app's version, the MSI it has just tested goes up with
its SHA-256 and the notes in `.github/release-notes.md`, modelled on
1.1.0's, from a second job that alone may write to the repository. Nothing
is signed.

It passed on its first run, and on the run after a one-line fix: 72 checks
against 1.2.0 with 1.2.1 as the upgrade, `LICENSE` in the program folder
among them. That is two more than 10-08's 70: the licence check, and,
most likely, the check that the app is still running after launch, which
the test counts only where Smart App Control is not enforcing (the
runner does not; a PC that enforces it blocks the unsigned app). The fix
was the checksum file: Git Bash's `sha256sum` on Windows wrote the name
as `*name`, binary mode, where 1.1.0's file has two spaces, so the
workflow now writes the line itself. The MSI is about 68 MB, and the two
runs' MSIs, built from the same payload, differ by 4 KB and in their hash, so
the SHA-256 that matters is the one the release job publishes beside the
file it attaches.

The workflow and the release notes went in without a review of their own,
so they had one the same day. Nothing in them was wrong, but some things
were tightened. The steps pasted the version and the MSI's name into their
scripts as `${{ }}` expressions; those now arrive as environment variables,
and the version read from the csproj must be two to four numbers, with no
leading zeros (bash arithmetic refuses `08` as a bad octal number, which
stops the step, and reads `010` as 8, which would give the upgrade build
1.2.9 where 1.2.11 was meant) and an optional suffix, before anything
evaluates it or names a file after it. The release job, the one that can
write, checks again that the tag is `v` and the csproj's version, though
the build job already stops on any other, and neither job's checkout
leaves its token in `.git/config`. The notes now say the installer is
unsigned on purpose, and how to check the download against its SHA-256,
with PowerShell's `Get-FileHash` as well as `sha256sum`. The lifecycle
test's `LICENSE` check now wants a file holding the GPL's text, not just
something of that name.

One gap was left in when the workflow runs. Its path filter named the
installer's files, its two scripts and the app, but not the agent or the
SDK, though the MSI carries both and the lifecycle test starts that
agent, so a change to the agent alone could have reached the `v1.2.0`
tag with no installer run behind it. The filter now names `agent/` and
`sdk/` too.

## A paired phone gets a key of its own (2026-11-05)

**11-05.** Pairing, part of the photo service since before 1.0.0, gave
the phone the agent's admin token: `/api/pair` answered with it, and
`/api/pair.svg` drew it into a QR code of `/?token=`, a link that signed
whoever scanned it in to the whole dashboard. A phone paired that way,
or anyone with a photo of the screen, could have deleted a share. No
phone app exists, and neither the browser dashboard nor the Windows app
shows that QR code, so nothing used pairing yet, and the answer could
change without breaking a client. The maintainer asked for it to be
fixed before 1.2.0.

Pairing is an exchange now. `/api/pair` issues a one-time code (128
random bits, claimable once within ten minutes, withdrawn by the next
code or by five wrong claims), and the QR code carries it after
`#pair=`, the part of a link a browser never sends, so it cannot reach
a log. The phone trades it at `POST /api/pair/claim`, which needs no
admin token, for a token of its own. That token travels in its own
header, `X-Device-Token`, so neither token can be taken for the other;
it opens the seven photo-upload routes and is refused on every other,
pairing and revocation included; and the agent keeps only its SHA-256,
in a file beside `config.json` that git ignores. The file is named
after the config beside it (`paired_devices.mock.json` for the
simulator's `config.mock.json`), so a phone paired with the simulator
cannot upload to the real agent in the same folder.
`GET /api/photos/devices` lists the phones and
`POST /api/photos/devices/revoke` unpairs one at once. A phone's last
use is saved at most once a minute, so a run of chunk uploads does not
rewrite the file each time. The access log hides `pair=` and `code=`
the way it hides `token=`, and a claim's body is never printed.

The suite's pairing check had asserted that the admin token came back;
it now asserts that a code does and the token does not. Thirty new
checks cover the code, the claim, the phone's token on and off the
photo routes, the saved file and revocation, and each one fails when
the agent's old `api.py` is put back.

## 1.2.0 goes out (2026-11-05)

**11-05.** With the recorded-fault fixes and the pairing fix on one branch,
the installer workflow built the 1.2.0 MSI from that branch's head, commit
1483dafaaaa29d8b633e20a2f1b2007cf1d9927b, together with a throwaway 1.2.1
for the upgrade leg, and ran the lifecycle test with both on GitHub's
`windows-latest` runner, which runs elevated: `test-installer.ps1: ALL PASS
(72 checks)`, exit 0, with `PASS  LICENSE present in the install folder`
for `C:\Program Files\Drobo Dashboard REBORN`. The MSI,
`DroboDashboardReborn-1.2.0-win-x64.msi`, was 71,438,336 bytes (68 MB; the
build reported 68.1 MB), SHA-256
`df14c1dd91126ead5a136c5c1cd32857a195facca168e8addbc18c29e8c94362`. Every
elevated run before 11-05, 1.1.0's included, was on the maintainer's PC;
this one, like the workflow's first runs earlier the same day, was on a
runner thrown away afterwards, and it is the run this release records. No
run on a tester's own PC, or on the maintainer's, has been
recorded for 1.2.0.

**11-05.** At the maintainer's direction everything under the changelog's
Unreleased heading, landed after 1.2.0 was numbered on 10-29 -- the
readability pass, the recorded-fault fixes of 11-02, the installer workflow
and the pairing fix -- was folded into 1.2.0 under its own sub-headings, and
the section dated 2026-11-05. Its opening note on renamed names now points
at the readability pass's list as well as the PEP 8 one. 1.2.0 is then
merged and tagged `v1.2.0`, and on that tag the workflow builds the MSI
again from the tagged tree, runs the same test and publishes that MSI on
this repository's Releases page with its SHA-256 beside it. Two builds of
the same payload differ by a few kilobytes and in their hash (see 11-05
above), so the hash that matters to a tester is the one published beside
the file, not the one recorded here.

## The start-up handoff, closed (2026-11-06)

**11-06.** The session that let the agent start before its Drobo, on
2026-09-26 (above), had ended with a handoff listing four items still
open, which the maintainer passed on once 1.2.0 was out. The first,
merging the work, was long done: the fix is in this history and true of
the code, and run again against loopback stand-ins in a namespace with
no network, an agent with nothing to find stayed up, raised the critical
alert, and picked up a stand-in that appeared later, with no restart.
Two more were fixed that day.

`run_agent.py --once` still ended in a Python traceback whenever it read
no Drobo: discovery finding nothing, a device that is provably not this
Drobo, a reply that is not a Drobo's, or nothing answering at a fixed
address. The handoff named the first two; all four did it. Monitor.start
answers each of them, but `--once` never calls it, and the 2026-09-26
fix had changed only Monitor.start. That mattered because
`testers-tasks.md` gives `--once` to a tester with a model this project
has never seen, and a traceback is what would have been pasted into an
issue. The reasons and the advice now come from one function both use,
so they cannot drift apart: `--once` prints them on stderr, without
"starting anyway", and exits 1, while anything else `connect()` raises
still surfaces as the bug it is, and a reading prints the same JSON as
before, with status 0. That includes a reading marked unreachable: if
the Drobo stops answering between being found and being read, the poll
turns the failure into such a reading, as it always has, so `--once`
reports it as JSON rather than as an error.

The other was a `ConnectionResetError` traceback in `test_agent.py`'s
output, which the handoff called pre-existing. It came from the agent's
own server, not the tests. An event stream ends only when its client has
gone, yet its connection stayed marked keep-alive, so http.server went
back to read a next request from a socket the client had reset: the
suite's early-closed stream did it, and so does every browser-dashboard
tab that closes or reloads. On Linux that read finds the end of the
stream and nothing is printed; on Windows the reset sticks, the read
raises, and socketserver prints a traceback into the agent's console. The
stream now closes its connection when it ends. That was traced on Linux,
with Windows' behaviour simulated, and GitHub's Windows test runner had
already been showing it: at 1.2.0 the Python suites' log there carries
the traceback, as `ConnectionResetError` (WinError 10054) in one run and
`ConnectionAbortedError` (WinError 10053) in another, and on the fixed
commit it carries none. It has not been watched on a tester's own PC.

The last item stays open: the fix has still not been tried on a real
boot with the Drobo out of reach, so it is listed below. The handoff
also left one choice with the maintainer. By that session's own account,
treating a reply that is not a Drobo's, at a fixed address, as "start
anyway" was its judgement call, not the maintainer's, and is the
maintainer's to reverse; the alternative is to stop at start-up with a
message, as a mismatched serial does. `test_agent.py` goes from 333
checks to 351. The twelve on `--once`'s four failures fail when the old
`run_agent.py` is put back, and the one on the stream's connection when
the old `api.py` is; the other five guard what must not change: a
`KeyError` from `connect()` still escapes, a reading still prints whole,
and a stream still lets go of its subscription.

## Ready for testers who did not build it (2026-11-08)

**11-08.** Before inviting Drobo owners to try 1.2.0, the maintainer
approved three changes aimed at someone arriving cold from a Release page.
The Release body, tracked as `.github/release-notes.md`, had assumed the
reader already knew the project: it now opens with what the software is
and that it is verified on a 5N only, links the tester guide, the tester
tasks and a new issue form, and carries the Smart App Control way round
that `TESTING.md` already gave, in its plain words. The installer
workflow still fills only its four placeholders, so nothing new in braces
was added.

The issue form, **Tester report**, asks for what `TESTING.md` had listed
as the most useful report, plus the app and Windows versions, and will
not submit until two boxes are ticked: serials, IP and MAC addresses, the
Drobo's name, share names and the tester's Windows user name removed, and
no packet capture attached. The user name was added the same day, after
review: the agent's window prints the path it saved the token file to,
and on an installed copy that path runs through `C:\Users\<name>\`. It
is stricter than `TESTING.md` had been, which said the diagnostics' contents
were harmless but yours to trim; the guide now says to remove them, so
the two agree. Blank issues stay allowed and no labels are set, since a
label the form names is not created for it.

The README gained three screenshots of the browser dashboard, the only
interface that can be run here. They were taken against the mock driver
inside a network namespace with no route out, so nothing could reach a
real Drobo, with Chromium at twice scale, getting past the token prompt
the way a user would. The overview stops at the drive bays on purpose:
the simulator also shows a cache battery, PSU and fan reading that a real
5N never gives, and a screenshot of them would promise what the hardware
does not deliver. The alert list shows a drive failed and another warned
through the simulator's own controls. Every string in every image was
read before it was kept: the simulator's name, serials and addresses
only, no token, host name or path. Screenshots of the Windows app remain
the maintainer's to take.

## Readiness for outside testers (2026-11-20)

**11-20.** Before inviting Drobo owners who did not build it, a
read-only audit of `main` at f939f86 asked one question: what would stop,
mislead or endanger an outside tester with a network Drobo in their first
session, or keep their report from reaching the maintainer in a usable
form? It ran four audits (first run, write safety, the feedback loop, the
release itself), each checked by a sceptic, edited nothing, and contacted
no Drobo. The maintainer then decided eight questions it raised. D1: the
writes never proven on hardware (share create, share delete, DroboApps
start and stop) are labelled, not hidden. D2: share writes on a Drobo
with user accounts get a warning in words, not a refusal. D3: nothing is
blocked by model in code; a Drobo that is not a 5N gets a true alert and
a note to stay read-only. D4: the Identify wording changes now. D5:
testers without GitHub, or with something sensitive, use GitHub's private
vulnerability reporting through a short `SECURITY.md`, the setting
switched on by the maintainer. That covers the sensitive half only: a
private report needs a GitHub account just as an issue does, so a tester
without one still has no route in the repository itself, and reaches the
maintainer only where the maintainer announces the release. D6: blank
issues are switched off. D7:
the update check stays dormant, and "Check for Updates" points at the
Releases page. D8: checking the mail server's certificate waits. The
fixes land as packages, one commit each, and the version stays 1.2.0
until the maintainer's own hardware session.

**Readings.** Three things a tester on real hardware would have seen
were not true. A 5N's status greeting carries no protection figure, so
`Capacity.protection_bytes` stayed at its default of 0 and both apps and
the diagnostics printed "Protection 0 B" beside real figures, which
reads as "my data is unprotected"; the agent now sends
`protection_reported`, false from the real driver and true from the
simulator, and all three say "not reported by this Drobo" (a Windows app
talking to an agent older than the key counts only a figure above 0 as
reported). The parser mapped `mDiskState` 0 to empty, so a present disk
reporting 0 was badged empty and raised nothing, the opposite of the
parser's own rule that an unknown code is a warning; presence is now
settled first, from `mStatus` 128 or no size, and after that only 16 is
healthy, so such a disk warns, once, with a note saying 0 has not been
seen on a healthy disk. And the pack status alert said a code had
"changed ... different from before", untrue on a unit seen for the first
time, since the baseline is one 5N's values; it now names where the
healthy value came from, says it may be normal on another model or
firmware, and asks for a report. That is the alert half of D3. Proven
here: every new check that can fail failed against f939f86's code, the 31
Python suites pass as before (the backup suite's two robocopy checks are
Windows-only, as always), flake8 is clean, and both .NET builds compile,
with the Windows test program's seven new checks run on Linux before its
Credential Manager section. Not proven here: the WPF Status tab drawing
the words, which needs Windows, and a populated bay reporting 0, which
has never been seen on hardware. No byte or XML sent to a Drobo changed.
The drive warning's text, which put the note's lowercase start after a
full stop, now capitalises it.

**Choosing the Drobo.** Nothing could change which Drobo a running agent
watched. The app's "type its address" box probed the address, said
"Found <name>", and Connect opened a dashboard that stayed unreachable;
with two Drobos the picker opened whichever one the agent already had
(M2 and S8, both reproduced at 1.2.0 with loopback fakes, the first as
`/api/discover?host=` saying `manual_found` true while `/api/status`
stayed unreachable). `POST /api/drobo/select` now makes the same
read-only identity check (one status-greeting read, no login), refuses
with one plain sentence and a fixed `reason`, saves the choice to
`config.json` before it switches, and repoints the running driver under
its reading lock. The rules are in `selection.py`: a first run accepts
what answers; the paired Drobo is accepted at any address; another
serial only when the request names it, which a picker row does with the
serial its own device reported. A change of serial forgets the previous
Drobo's alerts, reading and history with no notification, because kept
they would have been sent as "Resolved" by the new Drobo's first
reading. The Windows app's Connect calls the route and opens the
dashboard only once the agent has switched; the browser dashboard has
the same address box while the Drobo cannot be reached. The token page,
the banner and the "cannot reach" advice now name the config file the
agent really uses, say to disconnect a VPN, and name the probe tool only
where a tools folder exists (S9.1, S9.2). The package's review found one
thing the merge fixed: the app's reconnect at every launch went through
the route, so the first launch turned an mDNS-found address into a
permanent "manually-added" one, and replaced a fixed host name with its
IP, with no click; choosing the Drobo already being read, and
answering, now changes nothing, and two checks prove the file stays
byte for byte as it was (both fail on the package's own code). It also
found a limit left to the maintainer rather than guessed at: a page
already open on one Drobo sends its writes to whichever Drobo the agent
watches when they are sent, so after a switch a stale Shares or Advanced
Configuration page would write to the new one; `TESTING.md`,
`docs/capabilities.md` and the roadmap say so. Proven here:
`test_agent.py` went from 360 checks to 391, each new one failing on the
code before its fix; the Windows test program's ten new DroboSelection
checks run on Linux before its Credential Manager section (70 run here,
from 60); the 31 Python suites pass as before, with the backup suite's
two Windows-only robocopy checks; flake8 is clean and both .NET builds
give no warnings. Not proven here: the WPF picker itself, which needs
Windows, and any of it against a real Drobo or two. No device write was
added, no gate changed, and no byte sent to a Drobo is different.

**Unproven writes, labelled.** Share create, share delete and DroboApps
Start/Stop were offered in both apps, and nothing a tester could see
said that delete and Start/Stop had never been sent to a real Drobo, or
that create was applied once, before its acknowledgement fix, and not
re-run since (M3); a tester could have been the first person ever to
send one, on a device with no recovery tool, without knowing to use a
throwaway share. Identify promised the lights "should be blinking now",
which nobody had seen on this unit (S7). Under D1, D2 and D4 the writes
are labelled at the point of use, not hidden: the New share dialog and
the browser's "Add or remove shares", both delete confirmations, the
DroboApps card and panel, and the share-access editor each carry one
sentence saying what is unproven, and create and delete add that share
changes on a Drobo with user accounts are untried, because every share
write resends the user list as read and a populated one has never been
seen (S3, a warning in words with no refusal, since refusing would also
switch off share access, which is proven). Identify's card, in both
apps, now says the lights did not visibly change on the one 5N tested at
the setting this app sends and asks for a report; the agent's reply says
the same at interval 15, the one value watched, calls any other interval
untried, and on the simulator asks for nothing; the browser button reads
"Identify". The sentences live in `tester_notes.py` and `TesterNotes.cs`,
one place per front end, so they can be reworded after the hardware
session, and a `test_agent.py` check fails, naming the sentence, if the
two copies drift. Proven here: `test_agent.py` went from 391 checks to
394, `test_identify_api.py` from 31 to 35 and `test_shares_api.py` from
111 to 117, and with f939f86's `api.py` and `webui.py` restored, ten of
the thirteen new Python checks failed (the package's review); the 31
Python suites pass as before (the backup suite's two robocopy checks
are Windows-only); flake8 is clean; both .NET builds give no warnings,
which shows every `x:Static` name exists; and the Windows test
program's six new TesterNotes checks run on Linux before its Credential
Manager section (76 run here, from 70). Not proven
here: that the amber notes render as intended in the WPF windows, which
only a Windows PC can show, and the C# checks from the Credential
Manager section on, which only CI runs. Words only: no device write was
added, no gate moved, no byte or XML sent to a Drobo changed, and the
default Identify interval stays 15 until part F of the hardware session
has tried 900.

**Feedback, safe to send.** Three things stood between an outside tester
and a usable report. Copy diagnostics put the raw status JSON on the
clipboard, and the saved file held a summary followed by the same JSON,
so about fifteen identifying values appeared twice and testers were
asked to remove them by hand (S12); the last-chance error handler showed
the exception's message alone and dropped unobserved task errors (S13);
and "Check for Updates", dormant under D7, stopped at "switched off"
(C4). Masking chose placeholders of the same shape (`SERIAL-1`,
`DRIVE-SERIAL-n`, `ADDRESS-n`, `VOLUME-n`, `EVENT-n`, `NETWORK-n`), so a
report still reads as one Drobo with five drives, and two passes: the
fields known to identify someone first, then every other string in the
reply through a text scrubber, which is what catches an address inside
the agent's error or a field added later. Event-log messages are masked
whole because their line format is still undecoded and could hold
anything. "Drobo" on its own is not hunted for in text, so "the Drobo"
stays readable. The package's first review found that an identity
mismatch, where the agent's error quotes two serials and the device
block is empty, passed both serials and the other unit's name through;
the fix catches them by where they stand in that sentence and also seeds
the known values from the Drobo remembered in `settings.json`. The same
review moved the error dialog to a masked copy, because the dialog asks
for it to be posted; `app.log` keeps the full text, with only the token
and passwords removed, for the tester's own use. A turn worth recording:
the privacy pre-commit hook refused a test line asserting a literal
masked profile path, and the assertion was rewritten to check the path's
tail; later the guard's scan flagged a docstring with a literal
profile-path pattern, which was reworded. The hook was never bypassed.
The second review found two more, fixed in the merge: a profile folder
with a space in it (a local account's "First Last") kept its second word,
because the user-name segment stopped at whitespace; it now runs to the
next path separator, falling back to the old stop when nothing follows,
and two checks, written on drive D: so the guard's C:-only pattern does
not see a synthetic path, prove both. And one walk nested three deep
against the readable-code rule; its object case is a helper now, with
no change in behaviour. Proven here: the Windows test program runs 156
checks before its Credential Manager section, from 76 (78 from the
package, two from the merge's fix), all passing; `test_updates.py` gains
one check (99); the 31 Python suites pass as before, the backup suite's
two robocopy checks being Windows-only; flake8 is clean; both .NET
builds give no warnings. Not proven here: the new buttons, the clipboard
and the message box in the running app, and the handlers firing for
real, all of which need a Windows PC, and the checks from the Credential
Manager section on, which only CI runs. `TESTING.md` now says what the
masking does and still asks testers to read what they paste, the agent's
window most of all, which nothing masks. No device write was added, no
gate moved, and no byte or XML sent to a Drobo changed; the update check
stays dormant.

**One agent, and a launcher that waits for it.** The installed launcher
started an agent on every launch and then waited a fixed 2 seconds
(S4). The agent's window stays open after the app closes, so each later
launch added an agent: on Linux the second died in an `OSError`
traceback, reproduced here; on Windows, by its documented behaviour,
`SO_REUSEADDR` let it bind beside the first, both then reading the
Drobo. And 2 seconds is shorter than the agent's start-up, whose mDNS
browse alone listens for 3, so the app could open to "Is the agent
running?". The launcher now reads `agent.port` from the agent's
`config.json` and asks `/api/ping` with `curl.exe`, never through a
proxy: an answer means it opens only the app; otherwise it starts the
agent and waits up to about 20 seconds before opening the app anyway.
The agent claims its port before it looks for the Drobo and, when the
port is taken, says in one sentence whether another agent (exit 3) or
another program (exit 1) holds it, telling them apart by asking
`/api/ping` and waiting long enough for an agent that is itself still
starting. `Run-Agent.bat` takes exit 3 as nothing wrong and closes its
window with `exit`, not `exit /b`, because the launcher's `start` runs
it under `cmd /K`, where `exit /b` would leave a second window titled as
the agent's. The server leaves `SO_REUSEADDR` off on Windows and
`SO_REUSEPORT` off everywhere; `SO_EXCLUSIVEADDRUSE` was left out
because it can refuse a restarted agent for a minute or two. A bind now
happens before the handler class is filled in, so a refused second
server cannot replace a running one's monitor or token. In passing, the
Uninstall shortcut's tooltip, which claimed it removed the settings and
the token (S9), now says they are kept, and the MSI carries the shares
guide where the app looks for it, so the Tools tab's guide button,
hidden in every install until now, appears (C2). Proven here:
`test_agent.py` went from 394 checks to 406 after the merge (351 to 363
on the package's own base), and in the package's review, with the new
checks run against f939f86's agent code, nine failed (the traceback, and
the Drobo monitor started before the bind); an offline loopback run gave
the agent sentence with exit 3, the other-program sentence with exit 1
after the 15-second ping wait, and the first agent still answering; an
offline simulation of the launcher's `curl` flags against a mock agent;
the 31 Python suites pass as before, with the backup suite's two
Windows-only robocopy checks; flake8 is clean; and the Windows test
program is unchanged by this package. Not proven here: anything in the
`.bat`, `.ps1` and `.wxs` files, which this container cannot run, and
the Windows bind without `SO_REUSEADDR`. CI's `tests.yml` is where the
second runs, and `installer.yml` the rest: `windows/test-installer.ps1` gains
thirteen checks (about 85 with the upgrade leg, from 72), among them a
second launch, a second agent started by hand and a second
`Run-Agent.bat` started as the launcher starts it, each leaving exactly
one agent and one agent window. None of it has run on a tester's PC.
`TESTING.md` now says the launcher only opens the app when the agent is
already running. No device write was added, no gate moved, and no byte
sent to a Drobo changed.

**What the tester documents say.** The code packages fixed what the apps
show; this one fixed what a tester reads before and after (S9.3-S9.6,
S10, S11, S14, and the documents' halves of M2, M3 and S5-S7). Before
it, `TESTING.md` pointed at the README for the writes without saying
which were proven, told nobody how to type an address or to close the
agent before an upgrade, and described an uninstall that "waits for you
to press Enter", which Windows' own removal does not; `testers-tasks.md`
gave the sweep no address, said it asked for the cache battery (it
leaves that command out on purpose), claimed the agent logs every status
document (nothing keeps that sequence), asked for a drive to be pulled
for data nobody would see, and offered Identify, a write, on a page that
promises read-only steps; three documents gave three different rules
for pasting tool output; and `TESTING.md` promised "another way" to
report that did not exist. Now `TESTING.md` carries a table of every
change the app can make, each marked proven on a 5N or not yet proven
in the same sentences `tester_notes.py` and `TesterNotes.cs` show, an
"If your Drobo is not a 5N" section quoting the pack status alert's new
words, the typed-address route (VPN off and Refresh first) and the
two-Drobo behaviour from select-drobo, what to copy when the app never
gets past the first screen, the masked copy and the error dialog's
Ctrl+C from feedback, the launcher's wait from launcher, and an Updates
section. One paste rule is written the same in three places. Under D5
a short `SECURITY.md` sends anything that should not be public through
GitHub's private vulnerability reporting, and under D6 blank issues are
off, the issue chooser linking `SECURITY.md` instead. The report form
asks what was changed on the Drobo, what the app said, and whether it
found the Drobo; its option "No" is quoted, because YAML 1.1 reads a bare
No as false and GitHub requires string options. The release text says
reading is verified on a 5N and names the unproven writes. The merge
met two conflicts, in the README's write list and in `TESTING.md`, where
the code packages had already reworded the same lines; both sides were
kept, the package's fuller wording with the code packages' "both apps
say so", the two-Drobo reload advice moved under the new two-Drobo
heading, and the list of placeholders and the warning that the agent's
window is not masked carried into the new diagnostics section. The
merge also moved the last two "stops on its own" claims for Identify in
the documents, in `capabilities.md` and the roadmap, to the original
Dashboard's 15-minute warning (two SDK docstrings still say it), and added
`SECURITY.md` to the document index. Proven here: both issue-form files
parse as YAML with every option a string and every id unique,
`release-notes.md` dry-fills with `installer.yml`'s `sed` leaving no
placeholder, flake8 is clean, and the 31 Python suites and the Windows test
program are unchanged, since no code moved. Not proven here: how GitHub
renders the form, which shows only once it is on the default branch. The
private-report link was written as if private vulnerability reporting were
still to be switched on, and this said that had to happen before the link
reached `main`; the final review found it already on, a read-only query of
the repository's setting answering enabled on 2026-11-20. Nothing here is
code: no device write, no gate and no byte sent to a Drobo changed.

**11-21.** A final review of the six packages, made before any of them
was pushed, found records claiming more than had happened and tester
text that would send a tester wrong. Three records said CI had run or
proven checks that nothing had run: none of the commits had been
pushed, so neither `installer.yml` nor `tests.yml` had seen the
launcher's thirteen new checks or the check of the Windows bind; they
now say those checks run only there and had not run when this was
written. `TESTING.md`, which ships in the install folder, said the report
form asks testers to confirm they had read the diagnostics; its required
boxes ask instead whether the copy made for posting was used, or the
values removed by hand, and whether a capture is attached, and the
sentence now says that. It also told a tester whose port was taken to
change `agent.port` without saying that the Windows app does not follow
it: the app's AGENT box starts at `http://127.0.0.1:7420` at every launch
(readiness C3, still open), so it now gives that step, and first
suggests closing the other program and saying which one it was. The
README, the tester tasks and the agent's own refusal of a real address
on the simulator said to close "the simulated agent's window", which
does not exist: `START WITH SIMULATED DROBO (testing).bat` starts that
agent hidden and stops it when the app exits, and closing the launcher's
window instead can leave the simulator holding port 7420, where the
installed launcher, which only asks `/api/ping`, takes it for the real
agent, and the app's token is then refused. All three now say to exit
the app. The changelog pointed "above" for an entry that is below, and
two records still had private vulnerability reporting waiting to be
switched on. Proven here: `test_agent.py` went from 406 checks to 407,
the new one, on the simulator's advice, failing against the old
sentence; the 31 Python suites pass as before, the backup suite's two
robocopy checks being Windows-only; flake8 is clean. The .NET builds
and the Windows test program are unchanged, since no C# moved. No
device write, no gate and no byte sent to a Drobo changed.

**A change made against the other Drobo.** The weightiest thing the
final review found was left by the select-drobo package as a known limit:
once `POST /api/drobo/select` had switched Drobos, nothing in a write
said which Drobo it had been made against, so a front end that had not
noticed the switch sent its write to the new one. The review showed it
with loopback fakes and the SDK's delete replaced by a recorder: select
A, select B by its serial, then a delete of "Public" was recorded
against B. Neither front end guarded against it: the browser loaded its
share list once per page load, read it again only after its own share
changes, and never disarmed an armed delete when a reading brought
another serial; the app's delete dialog posted only the name and its
confirmation; and the app noticed a change of driver but not of
serial. The same could happen before the route existed, when an agent
with no serial pinned restarted on the other unit. Every route that
changes a Drobo now takes an optional `esa_id` and is refused with a 409
(`"reason": "another_drobo"`, labelled failed, both serials named)
before any device is touched, on the simulator as on the real driver, when
that serial is not the Drobo the agent watches; left out, the change goes
ahead, so nothing that calls the API today breaks. `/api/shares` says which
Drobo its list came from, which share changes send back, and a cached list
of the previous Drobo is no longer handed out after a switch: a read begun
before one could otherwise refill the cache the switch had emptied. The
browser sends the list's serial with share changes (an armed delete the one
it was armed on) and the latest reading's with the rest, and on a reading of
another Drobo disarms the delete, reads its shares and settings again and
puts its app list away until it is asked for; the app sends the same, its
share dialogs keeping the serial their row was read from, and on a reading
of another Drobo it clears the DroboApps list and the Settings tab and
re-reads the Advanced Configuration card. Proven here: `test_agent.py` went
from 407 checks to 421 and `test_shares_api.py` from 117 to 122, and with
the previous commit's agent code fifteen of the nineteen new checks failed,
the recorder catching the delete sent to B's share as the review's had; the
four that passed there check that a change named for the Drobo watched, or
naming none, still goes through. A live run of the agent on two loopback
greeting fakes, its share delete a recorder, answered the stale delete with
the 409 and recorded nothing, then recorded the delete made against B. The
page's own script, run in node against a stub page and a fake agent, sent
the first Drobo's serial with a delete armed before the switch, disarmed it
when the second Drobo's reading came, and sent the second's serial from the
list read again. The Windows test program gains five checks of the body and
of what counts as another Drobo, run here before its Credential Manager
section (161, from 156); both .NET builds give no warnings; the 31 Python
suites pass as before, the backup suite's two robocopy checks being
Windows-only; flake8 is clean. Not proven here: the WPF app sending the
serial and clearing its views, which only a Windows PC can show, and
anything with two real Drobos. No device write was added, no gate moved, and
no byte or XML sent to a Drobo changed.

**11-21.** The work was pushed and CI ran it for the first time. `tests.yml`
passed, Windows jobs included, so the check that a second agent cannot bind
beside the first on Windows now has a run behind it. `installer.yml` did
not: 80 of the lifecycle test's 85 checks passed, and the five that failed
were the new "exactly one agent" counts, each reading blank. The behaviour
they measure had worked -- a second agent started by hand closed with exit
status 3 and its one sentence, a second `Run-Agent.bat` window opened and
closed again, and the first agent still answered -- and the fault was in the
test: PowerShell unrolls a function's output, so a helper returning a list
of one hands back the bare process object, and Windows PowerShell 5.1 gives
a bare `CimInstance` no `.Count`. The answer that should have read 1 read
as nothing. Each call is now wrapped in `@()` again, with a comment saying
why.

## On the maintainer's own PC (2026-11-25)

**11-25.** The maintainer ran the current `main` on their own Windows PC
for the hardware session -- the repository cloned fresh, their own Python,
the app built from source -- and hit four things a tester would hit too.
No message in the Windows app could be selected or copied, because every
one was a WPF TextBlock, so an error could only be retyped into a report.
The first start answered "Token rejected": `START DROBO DASHBOARD.bat`
started the app before the agent, so on a fresh clone the app read no
token, the agent then wrote its first `config.json` with a new one, and
every request was refused until a restart (the installed launcher has
asked the agent's `/api/ping` first since 11-20; the repository's had
not). The message sent them looking in the wrong place: it named neither
the file the app had read its token from nor the other usual cause, a
second agent holding the port. And the agent then found no Drobo: the
fresh config had no last-known address, and the mDNS search came up empty
for a reason not yet known. One possibility, unconfirmed, is Windows
Defender Firewall blocking the replies to `python.exe`, which would meet
testers' installed copies too, since their bundled `python.exe` is new to
their firewall.

The first fix to land is in the Windows app. Every status, result and
error line a tester might quote is now a `CopyableText`, a read-only text
box styled to look like the TextBlock it replaced, with Ctrl+C, Ctrl+A and
a right-click Copy that takes the whole message when nothing is selected;
labels and headings stay plain text and no wording changed. Review of that
change found the Alerts tab emptying and refilling its lists on every
10-second poll, which with copyable messages destroyed a tester's
selection each tick, so the tab now leaves its rows alone while they read
the same (`AlertRowText`), at most about once a minute as "since" moves
on. On a 401 the app now reads its token again from the same files and
retries once if it changed, and a refusal that stands -- on the status
line, on the picker's search and, after the final review found it still
giving the old line, on a row's Connect, which does not read the token
again -- says which agent refused, which
config file the token came from (written with `%LOCALAPPDATA%`,
`%OneDriveCommercial%`, `%OneDrive%` or `%USERPROFILE%` in place of the
user's own folders, so a pasted report carries no user or employer name),
and that a second agent on the port is the other cause. The integration
review then found that the line under the device name, where that refusal
and the agent's "could not find the Drobo" error appear, did not wrap and
would have cut both off at the buttons; it wraps now. Proven here: both
.NET builds give no warnings, and the Windows test program passes 189
checks before its Credential Manager section, from 161, the 28 new ones
covering the refusal wording, the path shortening and the alert rows;
flake8 is clean and the Python suites are unchanged. Not proven here, and
left to a Windows PC or CI: how the copyable messages look and behave on
screen (alignment, selection, the menu, wheel scrolling, a selection
surviving a poll, how the longer refusal wraps) and the retry against a
real agent. The repository launcher's start order is not changed by this
fix; the next one changes it.

The second fix is the repository launcher's. `START DROBO DASHBOARD.bat`
now does what the installed launcher has done since 11-20: it asks the
agent's `/api/ping` on the port in `agent\config.json` before it starts
one, starts the agent in its own minimised window, "Drobo Dashboard
REBORN - Agent (from source)" (the same file run again with
`--agent-window`, which stays open with the reason if the agent stops), waits up to 20
seconds for an answer, and only then opens the app. An agent that already
answers is reported rather than taken for this checkout's: the launcher
starts none and says that an installed copy's or the simulator's agent
will refuse this checkout's token, and what to close. The final review
found that the agent's window, started as
`cmd /c ""<path>" --agent-window"`, left the path unquoted in the
launcher's own window, so an `&` in it -- likely under a work OneDrive
folder, whose name carries the organisation's -- would split the line and
start no agent; the outer quotes are now written `^"`, which keeps `&`,
`^`, `|`, `<` and `>` literal (the suggested `cmd /c call` would have
doubled a `^`). Review also found that its advice for the simulator, to
exit the app, fails in the very case the README warns about -- a simulator
whose launcher window was closed is a `python.exe` with no window that
exiting the app does not stop -- so the launcher, the README and the
tester tasks now end with restarting the PC. The same package gives the
agent's "could not find the Drobo" advice one sentence on the Windows
firewall, after the VPN check: that it may block the search's replies to
Python, and how to allow `python.exe` on the Private profile, never
Public; `TESTING.md` and `docs/running-the-agent.md` say it in short. It
is worded as a possibility, because why the search found nothing on the
maintainer's PC is still not known. Proven here: flake8 is clean,
`test_drivers.py` passes 92 checks, from 88, the four new ones on the
firewall sentence's place and its Private-only wording, `test_agent.py`
still passes 421, and the other Python suites and the Windows test program
are unchanged. Not proven here, because no cmd or PowerShell was at hand:
the launcher itself, which was read twice for syntax and is left to a
Windows PC; and whether the firewall was the cause at all, which only
another run on that PC can show.

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
worth doing: an elevated run of 1.2.0's lifecycle test on a real PC, since
its only recorded run so far is on GitHub's runner; re-run share create on
hardware since the acknowledgement fix; send share delete to a device for
the first time; send Identify at 900 while watching the unit; copy a real
Drobo share to an external drive once, which backup's grade waits on; start
and stop a DroboApp on hardware for the first time; start the agent on a
real PC before its Drobo can be found, which so far has been tested only
against simulated discovery; give the remaining command-channel reads the
whole-frame deadline the settings reads already have; one elevated install
of the fallback scripts into the real Program Files; record the original
Dashboard creating and deleting a user, which user management now waits on;
decide whether the email backend should check the mail server's certificate
(found 2026-11-02, while adding port 465) and whether a reply naming an XML
encoding Python does not know should be answered rather than drop the
connection (found 2026-11-05, in review), the two faults left in the
roadmap's list that wait on the maintainer (the faults the readability pass
recorded on 2026-10-29 were fixed on 2026-11-02 and the LED and DroboSync
hang-up found that day on 2026-11-05, and the list's two `esatm` readings on
2026-11-20, leaving those two alone); and then the phases that need what
this project does not have: a Mac for iOS, a second unit for DroboSync, a
redundant pack to degrade for the status codes. The licence is no longer
open: it was settled as GPL-3.0-or-later on 2026-10-19.

One question was closed rather than answered on 2026-09-05: the software will
not be code-signed, because this is not a for-profit project. That is a
deliberate trade — an unsigned installer warns on first run, and a machine
with Smart App Control switched on will not run the Windows app at all until
its owner switches SAC off. The browser dashboard, served by a Python
runtime that carries the Python Software Foundation's own signature, is
unaffected.
