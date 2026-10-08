# Patents as a protocol source

Suggested by the maintainer on 2026-07-28, and it is a good idea for a reason worth
stating: **a patent is a disclosure in exchange for protection.** The applicant
has to describe how the thing works. For hardware whose vendor is liquidated and
whose documentation is gone, that makes patents one of the few remaining sources
written by people who actually knew.

Everything here is public record. Reading a patent is exactly what patents are
for.

## The relevant portfolio

Assigned to Data Robotics Inc / Drobo Inc:

| Patent | Title | Why it matters here |
|---|---|---|
| [US 7,873,782](https://patents.google.com/patent/US7873782B2/en) | Filesystem-aware block storage system | **The core BeyondRAID patent.** Slot states and LED semantics — see below |
| [US 2007/0266037](https://patents.google.com/patent/WO2007128005A2/en) | Filesystem-aware block storage (application) | Adding/removing drives |
| [US 9,164,946](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/9164946) | Data storage RAID architecture system and method | Layout |
| [US 8,799,523](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/8799523) | Data storage architecture extension | Growing a pack |
| [EP 1815340](https://patents.google.com/patent/EP1815340A2) | Dynamically upgradeable fault-tolerant storage, mixed drive sizes | Why mixed drives work |
| [US 8,838,893](https://patents.google.com/patent/US8838893) | Journaling RAID system | Write path |
| [US 2012/0198152](https://patents.google.com/patent/US20120198152A1/en) | Asymmetrical block-level redundant storage | Single vs dual redundancy |
| [US 2014/0281228](https://patents.google.com/patent/US20140281228A1/en) | Accelerator cache based on memory availability | The mSATA cache slot |

## What US 7,873,782 already settled

**The four slot states are confirmed, and they are exactly the four this project
parses.** Independent corroboration of a model we had inferred:

| Patent state | LED | Our constant |
|---|---|---|
| Off — slot empty, array has space | dark | `STATE_EMPTY` |
| OK | green | `STATE_OK` |
| Degraded | amber | `STATE_WARNING` |
| Fail | red | `STATE_FAILED` |

**And it corrects a misreading of what amber means.** The patent defines
Degraded as *"Array is approaching a Fail condition; not enough space to maintain
redundant data in the event of a disc failure."* That is a **capacity** condition
about the whole array, not a health complaint about one drive. A dashboard that
renders amber as "this disk is unwell" is telling you the wrong thing — it means
"you are running out of room to stay protected".

**Flashing red/green means a relayout is in progress** after a drive change.
This project already reads `mRelayoutCount` from the greeting and alerts on it as
INFO ("rebuilding, this is normal") — which the patent confirms is the right
reading.

**What it does NOT contain:** any numeric status codes. No enumeration values, no
`mStatus` / `DNASStatus` decoding. The patent describes the state machine in
words and communicates it through LEDs. So it gives us the *semantics* of the
states without the *numbers* — which still leaves the numeric decode blocked on
observing a real degraded array, and that remains something not worth pulling a
healthy drive to obtain.

## Where else patents could still help

- **Capacity arithmetic.** This project inferred the array's redundancy mode
  from capacity arithmetic. US 8,799,523 and US 2012/0198152 are about exactly
  that and may confirm or correct it.
- **The mSATA accelerator slot.** US 2014/0281228 covers the cache. A bay
  reporting `RotationalSpeed = 0` was noted as odd, and this may explain it.
- **A future firmware fork.** BLUEPRINT §9 parks this behind "BeyondRAID's
  on-disk format is undocumented". These patents are the closest thing to
  documentation that exists.

## The honest caveats

**Patents disclose principles, not implementations.** They describe the method,
not the byte layout on disk or the wire format. Nothing here substitutes for a
capture.

**They do not cover the management protocol.** nasd, `DRINETTM`, `TMCmd` and the
103 commands are ordinary engineering, not patentable novelty, so they are absent
from the portfolio. The protocol work stands where it stood.

**Reading is not implementing.** This project builds a client that talks to a
Drobo; it does not implement BeyondRAID. That distinction matters if the firmware
fork in BLUEPRINT §9 is ever revisited: patents survive bankruptcy and get sold,
though the core filings date from 2005-2008 and a 20-year term puts most of them
at or past expiry now. Worth checking status before *implementing* anything
claimed — not before reading.

---

## Other people's software — 2026-07-28

The maintainer's second point: someone may already have solved parts of this. Worth
checking before reinventing.

### drobo-utils

[petersilva/drobo-utils](https://github.com/petersilva/drobo-utils) (and the
Python 3 fork [n3gwg/drobo-utils](https://github.com/n3gwg/drobo-utils)) is an
independent reverse-engineering of Drobo management, dating from 2008 and
unmaintained — the author no longer has hardware.

**It speaks a different protocol.** drobo-utils drives the SCSI/USB interface of
DIRECT-ATTACHED Drobos ("Drobo Management Protocol Rev A.0"). This project
speaks nasd over TCP to a *network* Drobo. Neither replaces the other, and its
existence does not duplicate our work.

But it holds enumerations, and Drobo had no reason to renumber its own constants
between its own interfaces. So its tables are a **second opinion**, testable
against readings we already hold.

### What survived that test, and what did not

**Partition scheme: CONFIRMED, and now decoded.** drobo-utils maps 0/1/2/3 to
None/MBR/APM/GPT. Our volume reports `partition_type = 3`. Independently, the
volume's 64 TiB ceiling *cannot* be MBR, which caps at 2 TiB. Two unrelated lines
of evidence agreeing is enough: `Volume.partition_scheme` now says GPT, with the
raw number still carried beside it.

**Unit status: REFUTED, and this is the useful half.** drobo-utils has a
bitfield: `0x0002 Red alert`, `0x0004 Yellow alert`, `0x0200 Relayout in
progress`, and so on. Tempting, because decoding `mStatus`/`DNASStatus` has been
blocked for weeks on needing a degraded array to observe.

It does not fit:

| Our field | Value | Against that bitfield |
|---|---|---|
| `DNASStatus` | 6 | decodes as **"Red alert &#124; Yellow alert"** — on an array that is provably healthy |
| `mStatus` | 98304 (0x18000) | no flag matches; bits 15-16 unexplained |

A healthy array decoding as a red alert is the tell. These are different fields
from the SCSI unit status, and forcing the fit would have put a fabricated
critical alert on the dashboard of a working Drobo. **The status codes stay
undecoded.**

**Partition format: NOT TRANSFERABLE.** Their table (NO FORMAT/NTFS/HFS/EXT3/
FAT32) contains no 64, which is what our device sends. A neighbouring table
being right is not evidence for guessing this one.

### The lesson worth keeping

Two of three mappings did not transfer. Borrowed enumerations are *hypotheses to
test against readings we already hold*, never answers to adopt — and the one
that fit only earned its place because a second, unrelated argument (MBR's 2 TiB
ceiling) agreed with it.
