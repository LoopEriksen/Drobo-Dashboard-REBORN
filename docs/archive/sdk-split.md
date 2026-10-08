# Splitting the Drobo SDK out of the agent

**Scoped 2026-07-27 — and executed the same day.** Kept as the plan it was:
`sdk/drobo_nasd/` is now its own package with a `pyproject.toml`, the agent
imports it, and `agent/drobo_agent/nasd/` no longer exists.

We accidentally built the only Drobo SDK in existence. `agent/drobo_agent/nasd/`
speaks the whole protocol: 103 commands, both channels, discovery by hardware
serial, credential redaction, and a safety guard. It is standard-library only,
so it has no dependencies to rot.

Right now it is buried inside one application. This is what it would take to
make it a thing other people could use — which matters at Phase 6, when the
repository goes public, because **the library is more useful to a stranger than
the dashboard is.** Anyone with an orphaned Drobo could build their own front
end on it; almost nobody wants our exact Windows app.

---

## What the coupling actually is

Measured, not guessed — by walking the import graph rather than reading around.

**There is exactly one boundary violation in the entire codebase:**

```
nasd/esatm.py  ->  ..models
```

Everything else in `nasd/` imports only from `nasd/`. That is the whole problem.
The reason to write it down is that "extract a library" normally means weeks of
untangling, and here it does not.

### The models divide cleanly

| Model | Used by | Verdict |
|---|---|---|
| `DriveSlot` | SDK + agent | **move** |
| `Capacity` | SDK + agent | **move** |
| `Volume` | SDK + agent | **move** |
| `PackHealth` | SDK + agent | **move** |
| `DeviceInfo` | SDK + agent | **move** |
| `Snapshot` | SDK + agent | **move** |
| `BatteryInfo` | agent only | stays — for now |
| `FanInfo` | agent only | stays — for now |
| `PowerInfo` | agent only | stays — for now |
| `PerformanceInfo` | agent only | stays — for now |
| `EventLogEntry` | agent only | stays — for now |
| `Alert` | agent only | **stays permanently** |

Six must move because both layers use them. `Alert` is genuinely an application
concept — a Drobo has no idea what an alert is — and stays.

The five marked *"for now"* need a judgement call rather than a measurement.
They describe **device** things (a fan, a PSU, throughput), so they belong in a
Drobo library on principle. They currently sit agent-side only because the
parsers that produce them return plain dicts and `drivers.py` assembles the
dataclass. **Recommendation: move them too**, and have the SDK parsers return
their own types. Otherwise the library returns dicts for some readings and
objects for others, which is the kind of inconsistency people notice
immediately and forgive slowly.

### One file is in the wrong place

`drobo_agent/shares.py` parses Drobo share configuration and has **zero
imports** — not one. It is pure protocol knowledge sitting at the application
layer, purely by accident of when it was written. Meanwhile `shareedit.py`,
which does the same kind of work, is correctly inside `nasd/`.

Move `shares.py` into the SDK.

### The tests are already sorted

Nine of the ten suites reference only `nasd/`. **Only `test_agent.py` is
actually an agent test.** So the test split is nearly free — the suites move
with the code they cover, and the count barely changes on either side.

---

## The plan

### Phase A — draw the line (half a day)

1. New package `sdk/drobo_nasd/`, a sibling of `agent/`, not inside it.
2. Move `nasd/*` in wholesale, plus `shares.py`.
3. Create `drobo_nasd/models.py` with the six device shapes (plus the five
   recommended above).
4. `agent/drobo_agent/models.py` keeps `Alert` and imports the device shapes
   from the SDK, so nothing downstream — `api.py`, `webui.py`, the Windows
   app — changes at all.
5. Move the nine SDK suites to `sdk/tests/`.

**Definition of done:** all twelve suites pass, and
`grep -r "drobo_agent" sdk/` returns nothing. That grep is the real test — it
proves the SDK cannot reach back up into the application.

### Phase B — make it installable (half a day)

6. `pyproject.toml`. Name: `drobo-nasd`. No dependencies, because there are
   none. Requires Python 3.10+ for `X | None` syntax.
7. `sdk/README.md` — written for a stranger with a Drobo, not for us. What it
   does, what it refuses to do, and a ten-line example that reads a device.
8. Version it independently. The SDK is stable protocol work; the dashboard is
   an application that will churn far faster.

### Phase C — make the API deliberate (one day)

9. Decide the public surface. Today everything is importable, including
   `_frame` and `_login_payload`, which are internals other people would come
   to depend on. Name the public API in `__init__.py`, prefix the rest.
10. Docstrings currently explain *the protocol* — invaluable, keep every word.
    Add what a *caller* needs: what raises, what returns None, what costs a
    network round trip, and what is rate-limited.
11. Carry the safety guard across intact, with its reasoning. `DO_NOT_SEND`,
    `SAFE_WRITES` and the four tests are not our house style — they are the
    single most important thing this library knows about irreplaceable
    hardware, and a stranger deserves them more than we do.

---

## What could go wrong

**Circular imports.** The obvious failure: SDK models importing agent code by
habit. Prevented structurally — the SDK is a sibling directory, so a stray
import fails immediately rather than working by accident.

**Two sources of truth for `Snapshot`.** If the agent keeps a copy "just for
convenience", they drift. The agent must re-export, never redefine. Worth a
test that asserts they are the same object.

**Import paths in the Windows app and installer.** The app talks HTTP and does
not care. But `build-installer.ps1` copies `drobo_agent/` recursively and would
silently ship an agent that cannot start. **This is the most likely thing to
break, and it will break at install time rather than build time** — so the
installer's lifecycle suite (`windows/test-installer.ps1`; 63 checks as of
2026-09-02) must run before this is called done.

**Losing the protocol commentary.** The docstrings in `nasd/` are the record of
how the protocol was recovered — including the corrections, which are the most
valuable part. They move verbatim. Any rewrite for a "cleaner" library voice
would destroy the actual asset.

---

## Effort, honestly

**Two days.** Most of it is Phase C, which is writing rather than moving.

Phases A and B are mechanical — one import to fix, a package to declare, nine
test files to relocate. The single boundary violation is what makes this small,
and it is small because `nasd/` was written as a library from the start, even
though nobody said so.

## When

**Before Phase 6, not now.** Phase 1 has real gaps the maintainer has named, and
none of them are helped by moving files around. The argument for doing it
before publication is that the package layout is what a stranger sees first,
and changing it afterwards breaks everyone who already imported it.

The argument for doing it sooner is that the boundary is clean *today* and
every new feature risks blurring it. `shares.py` landing at the agent level is
exactly that drift, already happened once.
