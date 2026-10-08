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
status document parsed cleanly.

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
