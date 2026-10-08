# What I need from you

> **A dated snapshot.** Written 2026-07-28 and amended through August; most of
> what it asked for has since been done and is recorded in
> [`development-history.md`](../development-history.md). The *live* list of things
> blocked on hardware or on a person is [`testers-tasks.md`](../testers-tasks.md),
> and the status of every capability is [`capabilities.md`](../capabilities.md).
> Kept because it records what was blocked, on what, and why.

Written 2026-07-28, after building everything that could be built without the
Drobo on the network.

The software side of Phase 1's remaining list is now blocked on things only a
person standing near the hardware can do. This is that list, most valuable
first, with the exact commands.

Nothing here is dangerous. No step formats, repairs, or flashes anything.

---

## 1. Get this PC on the Drobo's network — DONE 2026-07-28

Resolved. The PC and the Drobo are on the same subnet and the device answers.

The failure to watch for is easy to mistake for a dead device: Windows
auto-joins a different, higher-priority saved network, and the Drobo becomes
"unreachable" when nothing is wrong with it. If it happens, the check is:

```bash
py -m drobo_nasd.netcheck <your-drobo-ip>
```

from the `sdk` folder — it prints which networks this PC is on and whether the
Drobo is on one of them, and exits 0 when you are good. The fix is to raise your
home network's priority in **Settings → Network & Internet → Wi-Fi → Manage
known networks**, or turn off "Connect automatically" on whichever network keeps
winning.

(No specific network names are listed here, deliberately: SSIDs are mapped to
physical addresses by public wardriving databases, which makes a list of
someone's remembered networks more identifying than the private IPs this file
already avoids. The repo is treated as public.)

---

## 2. Record one session with the original Drobo Dashboard

*(Done. Recorded 2026-08-05 and 2026-08-06, plus a create/delete session on
2026-08-25. From those: LED brightness, the event-log fetch and share access
shipped; rename's mechanism is captured but unbuilt; user management never
appeared on the wire; Identify was answered on hardware 2026-08-07 — no
visible effect. The original ask is kept below.)*

**Blocks: LED brightness, renaming, the event log, share and user management,
and confirming whether Identify does anything at all.** Five features, one
sitting.

This is the big one. We recovered all 103 command names and IDs from the
firmware, but the firmware never says what goes *inside* each command's
parameters — and this project refuses to guess one, because a guessed parameter
is an unreviewed instruction to your storage. So those commands currently go out
empty and the Drobo either ignores them or answers empty back.

Watching the original Dashboard do the same jobs once settles all of them.

**Before you start:**
- be on the Drobo's network (step 1)
- **close Drobo Dashboard 3.5.0** — the capture needs to see it connect from scratch
- decide what you will temporarily rename the Drobo to (`ZZTESTZZ` is ideal —
  unmistakable in the bytes)

**Then double-click `CAPTURE DROBO COMMANDS (run me).bat`** in the repository
folder. It asks Windows for administrator rights itself and handles the rest.

(The earlier instruction here was to open an admin PowerShell and run
`tools\capture-session.ps1`. That fails twice over: an elevated PowerShell opens
in `C:\Windows\System32`, so the relative path is not found, and Windows'
default execution policy, `Restricted`, refuses the script even once found. The
.bat exists because both of those are the tool's problem, not yours.)

It prints a seven-step checklist, waits for you to read it, records for five
minutes while you work through it, then decodes the capture and prints every
command the Dashboard sent with its parameters.

The steps, in short — each is something the old Dashboard does in normal use:

1. Open Dashboard, let it connect
2. Change the LED brightness, then set it back
3. Rename the Drobo, then rename it back
4. Open the event log, and page back through it
5. Open a share's settings, change one user's permission, set it back
6. Open the DroboApps page
7. Click Identify — **and watch the unit**

**Three things to write down while you do it**, because they are not in the
bytes and I cannot recover them afterwards:

- **The exact words** the share permission dropdown offers (*Read only*?
  *Read/Write*? *No access*?) and which one you picked. This is the missing half
  of the access-code mapping.
- **What the lights did** on Identify — which ones, and whether you needed the
  faceplate off to see them. If nothing happened for the *original* Dashboard
  either, that is a genuinely useful answer: it means our command is fine and
  the feature never worked the way we assumed.
- **Which steps you skipped**, so I do not read a missing command as a failed one.

Then paste me the output. **Do not commit the capture file** — it is written to
your TEMP folder deliberately, and on an unencrypted protocol it contains
anything typed while recording.

---

## 3. The access-code measurement (more precise than step 2's version)

*(Done 2026-08-12 for code 1: measured as read/write on hardware and shown in
words. Code 0 stays deliberately hedged and code 2 has never been observed —
see `testers-tasks.md` #4.)*

**Blocks: showing share permissions in words instead of raw numbers.**

Step 2 covers this roughly. This does it exactly, by diffing the device's own
config before and after a single change:

```bash
py tools/access_codes.py before
```

Then in **Drobo Dashboard 3.5.0**, change one user's permission on one share to
something with a name, apply it, and note the word. Then:

```bash
py tools/access_codes.py after
```

It prints precisely which number moved. Tell me the word and I will record the
mapping in `sdk/drobo_nasd/shares.py`.

---

## 4. Decisions — answered 2026-07-28

All four are settled and built. Recorded in ROADMAP.md under "Decisions".

| Question | Your answer | State |
|---|---|---|
| **Where should app updates come from?** | Code it in, dormant until release | **Built.** Off by default; contacts nothing until a manifest URL is set. No source needs choosing yet. |
| **Desktop notifications?** | Yes — alerts only, each independently switchable | **Built.** Every switch defaults off. Narrows the "no toasts, ever" rule rather than reversing it. |
| **DroboSync?** | Code it in, dormant | **Built.** Reports "not configured" honestly; lights up if a second Drobo ever appears. |
| **Built-in file browser?** | No — Explorer is fine once mounted | **Struck from scope 2026-07-28; reversed 2026-08-19** — an in-app browser for a mounted share shipped with the explicit mount flow. The Shares tab still mounts shares as drive letters. |
| **Code-signing certificate?** | **No — decided 2026-09-05** | "We will not do code signing. This is not for profit." Ships unsigned: SmartScreen says Unknown publisher (*More info* -> *Run anyway*), and a Smart App Control machine must switch SAC off or use the browser dashboard, which SAC has no opinion about. |

> **Correction:** an earlier version of this file said a "free local exclusion"
> was an option for Smart App Control. There is no such thing — SAC has no
> per-app allowlist; it is on or off. That same note said turning it off could
> not be undone without reinstalling Windows. That was true when written and
> **is no longer true — corrected 2026-09-05**: since the April 2026 cumulative
> update (KB5083769) SAC can be switched off and back on from Windows Security
> > App & browser control.

### Still open

Nothing. The last entry here asked whether to approve `eCmdSetDimming` as a
safe write; it was overtaken rather than answered. LED brightness shipped in
`droboapps.set_dimming` **without** `eCmdSetDimming` ever joining
`SAFE_WRITES` -- it is still refused in `DO_NOT_SEND`. The write goes through
the vetted `eCmdSetConfig` transaction instead, which reads the live
`<General>` block, changes only `<Dim>`, echoes `FeatureStates` and
`SpindownDelay` back exactly as read, and refuses outright if the block holds
any field beyond those three. That read-modify-write is what makes it
reversible, which was the property the safe-write bar wanted all along.

---

## 5. Blocked on hardware I cannot conjure

| Feature | Needs | Realistic? |
|---|---|---|
| **iPhone app** | A Mac to build and sign it | The agent side is already done and waiting |
| **DroboSync** | A second Drobo | Not planned; the dormant code lights up if one ever appears |
| **Status-code meanings** (`mStatus`, `DNASStatus`) | A degraded array to observe | Would mean pulling a healthy drive. **I would not do this.** The parser already fails safe — unknown codes read as "warning" |
| **Fan / PSU / cache battery** | Nothing — a 5N plausibly has none of these to report | Already parked; they time out consistently and the firmware has no code path for them |

---

## What is already done and waiting for you

Built this session, working, tested, and it will light up the moment the Drobo
is reachable:

- **Network mismatch warning** — the dashboard, the alert and the device picker
  now say "this PC is on a different network" instead of "cannot reach the
  Drobo".
- **Firmware advisory** — your unit runs 4.3.1, one of the two builds Drobo
  withdrew before going bankrupt. Every screen showed that number; none of them
  said what it meant. Now one does, once, without nagging and without suggesting
  you flash anything.
- **Manual discovery by IP** — for networks that filter mDNS. Typed addresses
  are checked immediately and come back with the device's own name.
- **Diagnostics** — the report now opens with *why* the Drobo was unreachable,
  rather than an empty device block that reads as "your NAS is dead".
