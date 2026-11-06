# Things this project cannot test alone

Everything below is blocked on hardware or a situation the maintainer does
not have, not on code. This is a list for testers, not developers: each entry
says what is already built, what is unknown, and exactly what a person with
the right setup would run and report. Every step is read-only unless it says
otherwise, and nothing here risks data if the notes are followed.

Results come back as an issue on this repository: paste the decoded
output, never the capture file itself. Captures of this protocol can contain
the Drobo password in plain text.

## 1. DroboSync between two units -- needs two Drobos on one network

**Built:** reading DroboSync settings and logs (`sdk/drobo_nasd/drobosync.py`).
It reports "not configured" honestly on a single unit.

**Missing:** the *trigger* -- starting a sync -- has never been observed on the
wire, so its parameters are unknown and this project refuses to guess a write.

**What to do:** with two Drobos and the original Dashboard 3.5.0, run
`CAPTURE DROBO COMMANDS (run me).bat`, configure a sync between the two units,
start it, and stop it. Paste the decoded command list. The interesting
commands are `eCmdSetDroboSyncSettings` and whatever starts the run.

## 2. What the status codes mean -- needs a redundant pack you can degrade

**Built:** the parser reads `mStatus`, `DNASStatus` and `mDiskState` and fails
safe: any code it does not recognise is shown as a warning.

**Missing:** the meanings. A healthy array only ever shows the healthy codes.

**What to do -- only on a pack with redundancy, and only if it is backed up:**
start `py agent/run_agent.py`, pull one drive, wait for the Drobo to notice,
re-seat it, and let the rebuild finish. The agent logs every status document;
send the sequence of codes it saw, with the times. Do not do this on a
single-disk pack or one without a current backup.

## 3. Fan, PSU and cache-battery readings -- needs a chassis that reports them

**Built:** the commands, the alert rules, and the UI.

**Missing:** a device that answers. The 5N returns nothing for
`eCmdGetFanInfo`, `eCmdGetPowerInfo` and `eCmdCacheBattery`, and the firmware
appears to have no code path for them on that model.

**What to do:** on any other network Drobo, run
`py tools/nasd_sweep.py` (read-only; it sends nothing on the refused list) and
paste the output. If those three commands answer, the UI lights up with no
further work.

## 4. Permission code 2 -- needs Dashboard 3.5.0 and a spare share

**Built:** code 1 (read/write) is confirmed and writable. Code 0 is written
as "no access" but its meaning is deliberately left unconfirmed. Code 2 has
never been seen on the wire.

**What to do:** `py tools/access_codes.py before`, then in Dashboard 3.5.0
change one user's permission on one share to the level the dropdown calls
something other than Read/Write, apply, then `py tools/access_codes.py after`.
Report which number moved and the exact word the dropdown showed.

## 5. Other Drobo models -- needs any network Drobo that is not a 5N

**Built:** everything, verified on one 5N running firmware 4.x.

**Missing:** confirmation the protocol is the same elsewhere.

**What to do:** `py tools/drobo_probe.py mdns` and then the agent with
`--once`. Both are read-only. Report the model, firmware, and whether the
status document parsed cleanly. If `--once` can read no Drobo, it says why
in a few lines, starting `[agent] no reading taken:`, and stops; report
those lines instead. Read them first: like a reading, they can name your
Drobo's serial and its address.

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

**What to do:** start `START WITH SIMULATED DROBO (testing).bat` and report,
for each step, what you saw if it differs from what it says.

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
