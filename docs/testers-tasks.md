# Things this project cannot test alone

Everything below is blocked on hardware or a situation the maintainer does
not have, not on code. This is a list for testers, not developers: each entry
says what is already built, what is unknown, and exactly what a person with
the right setup would run and report. Every step is read-only unless it says
otherwise, and nothing here risks data if the notes are followed.

Results come back as an issue on this repository, through the
[Tester report](https://github.com/LoopEriksen/Drobo-Dashboard-REBORN/issues/new?template=tester-report.yml)
form. Anything that should not be public goes through the private route in
[`SECURITY.md`](../SECURITY.md) instead.

## What you need

- **For the installer route** (entries 2 and 5, and the two-Drobo part of
  entry 1): the installed app, from the
  [Releases page](https://github.com/LoopEriksen/Drobo-Dashboard-REBORN/releases)
  -- see the [tester guide](../windows/installer/TESTING.md). Nothing else.
- **For the `py tools/...` and `py agent/...` commands and the capture**
  (entries 1, 3, 4 and the source route in 5): a copy of this repository
  (on GitHub, **Code** -> **Download ZIP**, then unzip it), and Python 3 from
  [python.org](https://www.python.org/downloads/) with "Add Python to PATH"
  ticked during setup. Run each command in a Command Prompt opened in the
  repository's top folder. The installed app does not include `tools/` or
  `CAPTURE DROBO COMMANDS (run me).bat`, which runs a decoder from `tools/`
  with that Python.
- **Your Drobo's address**, where a command asks for one: the Windows app
  shows it on the **Settings** tab, under **The Drobo**, as **IP address**;
  the browser dashboard shows it in its **Network & shares** panel. Where a
  command below says `<address>`, type that address in its place, for
  example `192.0.2.10`.
- **For entries 1 and 4**: the original Drobo Dashboard 3.5.0 on a PC on the
  same network as the Drobo.

### What you can paste

One rule for every tool here, the same as in the tester guide:

- **Safe to paste after reading:** the console output of `nasd_sweep.py` and
  `drobo_probe.py`. Read it first and take out any address, name or serial it
  shows.
- **Paste only with names removed:** the decoded command list that
  `CAPTURE DROBO COMMANDS (run me).bat` prints, and the output of
  `access_codes.py`. They name your shares and user accounts, and can show
  serials and addresses.
- **Never post:** any capture file, or the JSON report `nasd_sweep.py` writes.
  They can hold your Drobo's serial, every drive serial, share and user names
  and your Drobo's address, and a capture of this protocol can hold the Drobo
  password in plain text.

## 1. DroboSync between two units -- needs two Drobos on one network

**Built:** reading DroboSync settings and logs (`sdk/drobo_nasd/drobosync.py`).
It reports "not configured" honestly on a single unit.

**Missing:** the *trigger* -- starting a sync -- has never been observed on the
wire, so its parameters are unknown and this project refuses to guess a write.

**What to do:** with two Drobos and the original Dashboard 3.5.0, run
`CAPTURE DROBO COMMANDS (run me).bat`, configure a sync between the two units,
start it, and stop it. Paste the decoded command list it prints, with share
names, user names, serials and addresses removed; never the capture file. The
interesting commands are `eCmdSetDroboSyncSettings` and whatever starts the
run.

**Also, with two Drobos and this app:** "Connect →" on a row now opens that
row's Drobo, and the agent watches it until you pick another (Change Drobo,
then Connect on the other row). Opening the app reconnects to the Drobo you
used last only if the agent is still watching it; if not, the app says so and
you press Connect on the row you want. Please report whether the dashboard
that opens shows the name and serial of the row you clicked. If the browser
dashboard is open while you switch, say too whether it moves to the new
Drobo's name and shares by itself within about ten seconds. Change nothing to
test this: a change made from a page still showing the first Drobo is
refused and never sent to the second, and nothing here needs a write.

## 2. What the status codes mean -- needs a pack that is degraded or rebuilding

**Built:** the parser reads `mStatus`, `DNASStatus` and `mDiskState` and fails
safe: any code it does not recognise is shown as a warning.

**Missing:** the meanings. A healthy array only ever shows the healthy codes.

**Do not pull a drive for this.** Nothing the agent shows or saves keeps the
sequence of codes: its history graph holds only overall state and numbers,
and the Alerts tab keeps an alert's latest words and when it started and
cleared. So a drive pulled on purpose would risk a pack for data nobody
would see.

**What to do, only if your Drobo is already degraded or rebuilding on its
own:** in the Windows app, use **Tools -> Copy diagnostics** now, and again
each time the Drobo's lights or the app's Status tab change, until the
rebuild has finished. Note the time of each copy. Send the copies, in order,
with the full text of every Disk pack health or drive warning the Alerts tab
showed. The copy made for posting keeps the status codes and replaces serials,
names and addresses with placeholders (1.2.0's Copy diagnostics is not
masked: with 1.2.0, remove them by hand); read it before you post all the
same.

## 3. Fan, PSU and cache-battery readings -- needs a chassis that reports them

**Built:** the commands, the alert rules, and the UI.

**Missing:** a device that answers. The 5N returns nothing for
`eCmdGetFanInfo`, `eCmdGetPowerInfo` and `eCmdCacheBattery`, and the firmware
appears to have no code path for them on that model.

**What to do:** on any other network Drobo, run

    py tools/nasd_sweep.py <address>

It is read-only: it sends only questions from a fixed list, and nothing on the
refused list. Paste the console output after reading it; do not post the JSON
report it writes. The sweep asks `eCmdGetFanInfo` and `eCmdGetPowerInfo`. It
leaves `eCmdCacheBattery` out on purpose, because that name could start a
battery self-test rather than report a status, so the cache battery stays
unasked. If either of the two answers with fields, say so: the alert rules and
the screens are built, and the reply shows what the agent would have to read
to fill them.

## 4. Permission code 2 -- needs Dashboard 3.5.0 and a spare share

**Built:** code 1 (read/write) is confirmed and writable. Code 0 is written
as "no access" but its meaning is deliberately left unconfirmed. Code 2 has
never been seen on the wire.

**What to do:** `py tools/access_codes.py before --host <address>`, then in
Dashboard 3.5.0 change one user's permission on one share to the level the
dropdown calls something other than Read/Write, apply, then
`py tools/access_codes.py after --host <address>`. Report which number moved
and the exact word the dropdown showed. Its output names your shares and
user accounts: replace them (for example with share-1 and user-1) before you
paste it.

## 5. Other Drobo models -- needs any network Drobo that is not a 5N

**Built:** everything, verified on one 5N running firmware 4.x.

**Missing:** confirmation the protocol is the same elsewhere.

**Expect some things to look odd.** On a Drobo that is not a 5N, or on
firmware other than 4.3.1, you may see one or more Disk pack health warnings
that start "This Drobo reports device_status=..." (or disk_pack_status,
dnas_status, status_ex). The only healthy values this software has seen come
from one Drobo 5N on firmware 4.3.1, so on your model these may be perfectly
normal. Please include the full text of each such warning in your report. The
bay list may also show an extra empty bay. Keep your first session to
reading: do not use share create, share delete, DroboApps start/stop, or the
Advanced Configuration card.

**What to do, with the installer:** install the app, pick your Drobo, and
send a Tester report with **Tools -> Copy diagnostics** (read it first), your
model and firmware, and any warnings as above. If the app finds no Drobo,
follow "If it does not find your Drobo" in the
[tester guide](../windows/installer/TESTING.md); if nothing works, paste the
lines from the **Drobo Dashboard REBORN - Agent** window instead, after
reading them, since that window is not masked.

**What to do, from a copy of the repository:** `py tools/drobo_probe.py mdns`
and then `py agent/run_agent.py --once`. Both are read-only. Report the model,
firmware, and whether the status document parsed cleanly. If `--once` can
read no Drobo, it says why in a few lines, starting
`[agent] no reading taken:`, and stops; report those lines instead. Read them
first: like a reading, they can name your Drobo's serial and its address.

**Also useful, from any Drobo:** if any bay that holds a disk shows a warning
with "disk state code 0 (not seen on a healthy disk)", please report it with
your diagnostics file. Nobody has seen that code on a populated bay, and this
software now treats it as a warning instead of an empty bay.

**Identify, on any real Drobo, after your first, read-only session:** this
step is not read-only. It sends Identify, the one command on the safe list,
which changes only the Drobo's front lights. Press **Identify** (Tools tab)
and say in your report whether its front lights blinked, then press
**Stop**. On the one Drobo 5N tested so far, the lights did not visibly
change at the setting this app sends. Do not use the simulated Drobo for
this: it has no lights to watch.

## 6. The iOS app -- needs a Mac

**Built:** the entire agent side -- pairing with a QR code, hash-first dedupe,
resumable chunked upload for large videos, capture-date preservation.

**Missing:** the app. Xcode is macOS-only.

**What to do:** nothing a tester can run -- this one waits on a Mac, and it is
listed here so the gap is not mistaken for an oversight. What iOS permits is
already settled: no third-party app may run continuously in the background, so
the realistic behaviour is that photos back up when you arrive home (geofence
or Wi-Fi wake) and catch up overnight while charging. No iOS app can do better,
and none of that is a limit of this project's agent.

## 7. Windows app screens -- needs a Windows PC

**Built:** the Windows app. Its rules that need no window (notifications,
saved logins, settings, JSON reading, write outcomes) are checked by
`windows/DroboDashboardReborn.Tests` on every push; the screens themselves
are not, because a test program cannot drive WPF, and nobody on this
project has a Windows PC at hand when these change.

**Missing:** someone pressing the buttons. Every step below runs against the
simulated Drobo, so no Drobo is needed and nothing is sent to one, unless it
says otherwise.

**What to do:** start `START WITH SIMULATED DROBO (testing).bat` from a copy
of the repository (the installer does not include it) and report, for each
step, what you saw if it differs from what it says. The simulated agent only
ever shows the simulated Drobo, and it has no window of its own: it stops
when the app exits. To use your real Drobo, exit the app (with Exit on its
tray icon, if closing its window only hides it), which stops the simulated
Drobo, then start START DROBO DASHBOARD.bat, or Drobo Dashboard REBORN from
the Start menu if it is installed. Do not close the launcher's window
instead: that can leave the simulator running unseen on port 7420; the real
launcher then takes it for the real agent, and the app says its token was
rejected.

1. **Change Drobo finishes its search** (fixed 2026-11-02). Open the
   simulated Drobo, press **Change Drobo**, and wait. The picker must leave
   "Searching..." and list the Drobos it found. Then, on the picker, add
   an address under **ADD A DROBO MANUALLY**, untick **Automatically
   search the network for Drobos**, open a Drobo and press **Change
   Drobo** again: the picker must show the address you added, not
   "Searching...".
2. **Answers that arrive late stay with the Drobo they were for** (fixed
   2026-11-02). The fault itself needs an agent that answers some
   requests and stalls on others, which a tester cannot arrange, so this
   checks that the guard does no harm. Open the simulated Drobo. In
   Resource Monitor's CPU tab, right-click the agent's `python` process
   and choose **Suspend Process**, press **Change Drobo**, then **Resume
   Process** within five seconds. The picker must list the simulated
   Drobo, without "Could not search the network", and opening it again
   must show its shares and alerts as before.
3. **Polling resumes after a stall** (fixed 2026-11-02). With the
   simulated Drobo open, suspend the agent's process as in step 2 for
   40 seconds, watching the footer: "Last attempt failed at ..." may
   change at most once every 10 seconds, never several times at once.
   Resume the process: within 20 seconds the footer must go back to a
   normal reading.
4. **A share that connected stays connected when its login cannot be
   saved** (fixed 2026-11-02). Real Drobo only, because the simulator
   switches saving off, and only if Windows ever refuses to save a login
   for you: there is no safe way to make it refuse on purpose. If **Connect
   as...** with **Save this login on this PC** ticked shows a saving error,
   the buttons must read **Close** and **Try saving again**; **Close** must
   leave the share listed as connected, and **Try saving again** must not
   show a connection error. Report the saving error's exact words.
5. **A write whose answer never comes is "may have been done", not a
   failure** (fixed 2026-11-02). Open the simulated Drobo, sign in to
   **Advanced Configuration** on the Tools tab, suspend the agent's
   process as in step 2, and press **Apply** beside brightness. After
   about two minutes the line under it must turn amber and say the change
   may have been done; it must not be red, and must not say "Could not
   reach the agent". Resume the process. Then end the agent's `python`
   process (Task Manager, Details tab, **End task**) and press **Apply**
   again: that one must be red, "Could not reach the agent", straight
   away.
