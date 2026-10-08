# Array notes — what the greeting says about a disk pack

What the `<ESATMUpdate>` greeting (see `docs/protocol-map.md`) reported about a
healthy pack on a Drobo 5N when it was first read live, on
2026-07-24, and what can and cannot be worked out from it. Everything under
"Measured" is a field read straight off the device. Everything under "Reading
the numbers" is inference from those fields, marked as such — this protocol
doesn't expose SMART data or a redundancy-mode flag, so some things below are a
well-supported guess, not a fact the device stated.

> **Edited for the public release.** The original snapshot also recorded the
> maintainer's own array: which drives, their sizes and firmware revisions, and
> how much of the pack was in use. That described one unit rather than the
> protocol, so it was removed. What is left is what the readings say about the
> greeting itself.

The device serial (`mESAID`) and the disk pack ID are deliberately not printed
here — see what `ROADMAP.md`'s Phase 6 keeps out of the tree. The pack ID
identifies one specific set of drives (and changes if they are repacked), so it
is kept out of the source tree along with the serial. Read your own with
`py agent/run_agent.py --once`.

---

## Measured

**Capacity.** Each slot reports its own raw size (`mPhysicalCapacity`); the
pack reports usable, used and free figures (`mTotalCapacityProtected`,
`mUsedCapacityProtected`, `mFreeCapacityProtected`). Raw capacity for the
whole pack is the sum of the per-slot sizes.

**Per bay.** Each populated slot carries the drive's make and model string
(`mMake`), its serial (`mSerial`) and its firmware revision (`mDiskFwRev`).
Revisions can differ from drive to drive within one pack; the greeting reports
each drive's own.

**Health signals** — on a healthy pack, every drive, no exceptions:

- `mErrorCount` — 0
- `mDiskState` — 16 (healthy)
- `mRelayoutCount` — 0
- `mDoubleDegradedCnt` — 0
- `mDiskPackStatus` — 0

Thresholds reported by the device: `mRedThreshold` 9500, `mYellowThreshold`
8500 (units not yet confirmed against a real alert — noted for completeness,
not yet load-bearing anywhere in the agent).

`RotationalSpeed` read 27 on most drives, but **0 on one**. Recorded as
observed; not yet explained (see below).

Per-drive **temperature** is not measured — `mTemperature` is `0` on every
slot, which `esatm.py` already treats as unknown rather than a literal 0°C. See
`docs/protocol-map.md` for the full writeup; this file is about what the
greeting says about a pack, not the protocol gap.

---

## Reading the numbers

### Redundancy: inferred from capacity, not reported

The device doesn't report its redundancy mode directly, but the capacity
figures can stand in for it. With equal-sized drives:

- **Single-disk** redundancy (survives one drive failure, BeyondRAID's older
  default) leaves roughly *(number of drives − 1) × drive size* usable.
- **Dual-disk** redundancy (survives two simultaneous failures) leaves roughly
  *(number of drives − 2) × drive size* usable — one more drive's worth of
  capacity, in exchange for surviving a second failure.

Compare the measured usable figure with both estimates. On the pack this was
first read from, it landed within a few percent of one and nowhere near the
other — a strong enough gap to state as a working conclusion, not a
coin-flip. It is still an inference: this protocol doesn't expose a
`RedundancyMode` field the way it exposes `mDiskState`, so treat a result
from this method as "very likely" rather than "confirmed." **What would
confirm it outright:** either a captured config document that names the
redundancy setting explicitly, or watching usable capacity respond correctly
if a drive is ever added or removed (single-disk math and dual-disk math
diverge further as the pack grows, so a second data point would settle it).

### What the drive fields can and cannot say

The one drive reporting `RotationalSpeed = 0` reported every health field above
exactly as the others did, so a 0 there is not, on its own, a failure signal.
This protocol does not expose SMART data (power-on hours, reallocated sectors,
wear), so **actual drive age and wear are genuinely unknown from what we can
read.** A firmware revision or an odd `RotationalSpeed` is at most a "keep an
eye on it" note, never a failure prediction.

### What a pack is not

BeyondRAID packs are readable only by another Drobo — the on-disk format is
undocumented and proprietary, and there is no tool outside a Drobo unit that
can mount one. That means redundancy protects against **drive failure**, not
against the Drobo itself dying, a fire, theft, or any other whole-unit event.
It is not a backup. (The backup feature shipped 2026-08-01 — plan, confirm,
then robocopy, from the Tools tab — so the tooling exists.)
