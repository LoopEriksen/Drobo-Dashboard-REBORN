# Command surface: what answers with only the plain login

**Measured live, 2026-07-26**, with [`tools/nasd_sweep.py`](../tools/nasd_sweep.py)
against the project's Drobo 5N. This answers the question the RSA
challenge-response has been blocking: *exactly* how much of the original
Dashboard can be rebuilt today, without cracking that handshake.

## Method

- 39 commands, a hardcoded allowlist of names (never an id, never a "starts
  with Get" heuristic -- see the safety rationale in `nasd_sweep.py`'s module
  docstring). Every name resolved to its numeric id through
  `commands.COMMANDS` at run time.
- One fresh TCP connection and one fresh plain login (device serial, sent
  twice -- `config_cmd.py`'s confirmed shape) per command. No crypto exchange
  attempted.
- Every command sent with an empty `<Params></Params>` body -- no parameters
  were guessed.
- 6 s timeout per command, 0.4 s pause between commands, hard stop on three
  consecutive timeouts (never triggered).
- Every response was redacted (`config_cmd._strip_secrets()`-equivalent, run
  before anything was printed or written anywhere) before this document or
  anything else was written. The full redacted response set lives in a
  private folder outside the repository, and is not tracked.
- After the sweep, the device was reconnected to on the status port (5000)
  and confirmed to still push its normal `<ESATMUpdate>` greeting.

**Result: the device is healthy after the sweep.** The post-sweep greeting
check succeeded.

## Results: 7 answered, 29 empty, 1 refused, 2 timed out

| Command | ID | Result | Fields returned (names only) |
|---|---|---|---|
| `eCmdGetAdmin` | 1 | empty | -- |
| `eCmdGetVersion` | 7 | empty | -- |
| `eCmdGetDimming` | 9 | **answered** | `ResultDetails` (single scalar -- dimming level) |
| `eCmdGetDiskPackId` | 15 | empty | -- |
| `eCmdGetKernelInterfaceInfo` | 20 | empty | -- |
| `eCmdGetConfig` | 30 | **answered** | `DRINASConfig` (empty shell -- see note below) |
| `eCmdGetFirmwareInfo` | 32 | empty | -- |
| `eCmdGetDroboSyncSettings` | 35 | empty | -- |
| `eCmdGetDroboSyncSummaryLog` | 37 | empty | -- |
| `eCmdGetDroboSyncDetailedLog` | 38 | empty | -- |
| `eCmdGetDroboSyncLog` | 39 | empty | -- |
| `eCmdGetReservedDriveLetters` | 42 | empty | -- |
| `eCmdGetDemoModeInfo` | 43 | **answered** | `FirmwareInfo` &gt; `DemoMode`, `ScaleFactor` |
| `eCmdGetUserEmailSettings` | 46 | empty | -- |
| `eCmdGetDevices` | 47 | empty | -- |
| `eCmdIsAutoDiscoveryEnabled` | 54 | empty | -- |
| `eCmdGetManualDiscoveryIPList` | 57 | empty | -- |
| `eCmdIsSCSIConnAvailable` | 59 | empty | -- |
| `eCmdGetSysInfo` | 61 | **answered** | `SysInfo` &gt; `UpTime`, `Temperature` |
| `eCmdGetPerformance` | 62 | **answered** | `Performance` &gt; `Iops`, `ReadThroughout`, `WriteThroughout`, `TierIOps` |
| `eCmdGetFanInfo` | 63 | **timed out** | -- |
| `eCmdGetPowerInfo` | 64 | **timed out** | -- |
| `eCmdGetControllerCard` | 65 | empty | -- |
| `eCmdGetExpanderCard` | 66 | empty | -- |
| `eCmdGetOSInfo` | 69 | empty | -- |
| `eCmdGetTMDiagFilePath` | 72 | empty | -- |
| `eCmdGetLunInitatorExtraInfo` | 74 | empty | -- |
| `eCmdGetDroboAppsStatus` | 77 | **answered** | `DroboApps` &gt; `SDKVersion`, `App`[] &gt; `Name`, `Version`, `Description`, `Stopped`, `ActiveStatus`, `WebUI` |
| `eCmdGetAdminConfig` | 80 | empty | -- |
| `eCmdGetSharesConfig` | 81 | empty | -- |
| `eCmdGetEventLogs` | 83 | empty | -- |
| `eCmdGetSessionIPAddress` | 84 | empty | -- |
| `eCmdGetAllDevicesStatusXML` | 85 | empty | -- |
| `eCmdGetRegisteredEmail` | 88 | empty | -- |
| `eCmdGetUserGroupList` | 90 | **answered** | `ResultDetails` (empty -- no custom groups configured on this device) |
| `eCmdGetUserEventLog` | 91 | **refused** | connection dropped after the command was sent (0-byte reply, not a clean empty result) |
| `eCmdGetDeviceNamesToShutdown` | 93 | empty | -- |
| `eCmdGetDASDeviceNamesWithMountedVolumes` | 97 | empty | -- |
| `eCmdGetDeviceMountedVolumesCount` | 98 | empty | -- |

Every one of the 39 logged in successfully -- "empty" means the login
succeeded, the command was accepted, and the device replied with a
zero-length result body. As `docs/protocol-map.md` already notes, an empty
body is ambiguous: it can mean "nothing to report on this model" or "you're
not authorized for this," and the two cannot be told apart without solving
the RSA handshake.

### Notes on individual results

- **`eCmdGetConfig`** answered but carried no real data, because this sweep
  deliberately sends empty `<Params></Params>` and never guesses parameters.
  `sdk/drobo_nasd/config_cmd.py` already knows the real shape --
  `<Params><Network>Network</Network></Params>` (and the `DRIShareConfig` /
  `DRINasAdminConfig` variants) -- and gets full data back. This sweep's job
  was to confirm the command answers *at all* under a bare login, which it
  does; `config_cmd.py` remains the right place to actually fetch config data.
- **`eCmdGetSysInfo`, `eCmdGetPerformance`, `eCmdGetDroboAppsStatus`,
  `eCmdGetDemoModeInfo`, `eCmdGetUserGroupList`, `eCmdGetDimming`** all
  answered with real data under a plain login in this run. This is a
  materially different result from `docs/protocol-map.md`'s earlier
  "Two privilege tiers" measurement (`eCmdGetSysInfo` succeeding roughly
  1 run in 6, most of the others called "tier 2" and gated behind the RSA
  exchange). Nothing here proves the crypto gate isn't real -- but it is not
  a hard gate on these commands, at least not consistently. **This needs a
  repeat sweep (several runs over time) before anyone designs a feature
  around "always available" vs "occasionally available."** Treat today's
  successes as "reachable," not yet as "reliable."
- **`eCmdGetFanInfo` / `eCmdGetPowerInfo`** timed out rather than answering
  empty or being refused -- the device accepted the login and the command
  frame and then never replied at all. This matches `protocol-map.md`'s
  earlier observation for these two exactly ("no reply at all"). Whether a 5N
  even has a fan/PSU sensor to report is still unknown.
- **`eCmdGetUserEventLog`** stands out: every other non-answering command came
  back as a clean zero-length result. This one caused the connection to be
  torn down instead (a 0-byte header read where 16 bytes were expected). That
  is a different failure mode from "empty" and worth treating as its own
  category rather than folding into "empty."
- **`eCmdGetDroboAppsStatus`** is the richest response by far: a full
  inventory of every installed DroboApp (name, version, description,
  running/stopped state, and web UI path where applicable).

## What this means we can build now

Provably reachable with nothing but the plain login -- no crypto handshake
required -- as of this measurement:

1. **Real device temperature and uptime** (`eCmdGetSysInfo`). The status
   greeting on port 5000 always reports `mTemperature` as `0`
   (`docs/protocol-map.md`); this command is the only known source of an
   actual reading. This closes the single biggest known gap in the existing
   dashboard.
2. **Installed DroboApps, with version and running state**
   (`eCmdGetDroboAppsStatus`) -- an "Apps" panel becomes possible without any
   new protocol work.
3. **Live performance counters** (`eCmdGetPerformance`) -- IOPS and
   read/write throughput, for an activity graph.
4. **LED dimming level** (`eCmdGetDimming`) -- minor, but confirms the
   read side of the existing brightness control is reachable.
5. **Demo-mode / firmware scale-factor flags** (`eCmdGetDemoModeInfo`) --
   low value on its own, but confirms the path.
6. Everything `eCmdGetConfig` already delivers in production
   (`config_cmd.py`): network config, share list, admin config -- unchanged
   by this sweep, just reconfirmed still working.

## What still needs the RSA handshake (or remains unknown)

- **Fan and power-supply status** -- both commands accept the login and then
  go silent. No data has ever been obtained from either, with or without
  crypto. Still genuinely unknown whether a 5N reports these at all.
- *(Event logs settled 2026-08-05: the sweep asked `eCmdGetEventLogs` (83)
  with empty Params, which is not what the Dashboard does. It uses
  `eCmdGetUserEventLog` (91) with `<Path>/uel.log</Path>`, which answers with
  the real log; shipping since 2026-08-21. The row below is as first written.)*
- **Cache battery, controller/expander card, event logs, drive-shutdown
  names, and the rest of the "empty" rows above** -- all of them logged in
  and were accepted, but returned nothing. Cannot be told apart, from the
  wire alone, between "not applicable to this chassis" and "needs the crypto
  step." That ambiguity is unchanged from `docs/protocol-map.md` and this
  sweep does not resolve it.
- **Reliability of the six commands that answered today but were previously
  measured as gated** -- needs repeated sweeps over time (different times of
  day, after device restarts, etc.) before any feature is built assuming they
  will always answer.

---

# Follow-up, same day: temperature is real, and the command port can be worn out

Two findings that came from chasing `eCmdGetSysInfo` immediately after the
sweep above. Both change how this project should treat the command port.

## 1. The reply encoding was the whole reason temperature looked gated

`eCmdGetSysInfo` needs **no handshake, no key, and no firmware change.** It
returns a real chassis temperature. This project spent weeks believing
otherwise, and the reason was a parsing mistake, not a protocol barrier.

`eCmdGetConfig` puts real XML *child elements* inside `<ResultDetails>`.
`eCmdGetSysInfo` puts an **entity-escaped XML document there as text**:

```
<ResultDetails>&lt;?xml version=&quot;1.0&quot;...&gt;
&lt;SysInfo&gt;
    &lt;UpTime&gt;1521745&lt;/UpTime&gt;
    &lt;Temperature&gt;36&lt;/Temperature&gt;
&lt;/SysInfo&gt;
</ResultDetails>
```

Anything searching for a `<Temperature>` element finds nothing and records the
command as unanswered. That is what produced the earlier "succeeds about 1 in
6" figure. Unescape the inner document and parse it a second time and the same
replies contain a temperature every time.

**Measured: 15 consecutive calls, 15 temperatures, all 36 °C.** Uptime came
back too — 1,521,745 seconds, about 17.6 days.

Implemented in `sdk/drobo_nasd/sysinfo.py`, which handles both reply
shapes so a future firmware that changes its mind still works.

**Check this first if any other command looks flaky.** Several of the "empty"
rows in the table above were classified by looking for elements that may exist
only in escaped form. That table may understate what the device gives up.

That suspicion was correct — see the re-sweep below.

---

# Re-sweep, 2026-07-26: the escaped encoding was hiding two more commands

The sweep tool counted fields by walking child elements, so a reply whose whole
payload is an escaped document counted as **zero fields**. It was taught to
unwrap them (`_unwrap_escaped` in `tools/nasd_sweep.py`) and the full allowlist
was run again against the live unit.

Result: **7 answered, 29 empty, 0 refused, 3 timed out**, and the device passed
its post-sweep health check. Same headline count as before, but the *contents*
changed — three of those seven only became visible once the tool could see
inside an escaped document:

| Command | id | What it actually returns |
|---|---|---|
| `eCmdGetSysInfo` | 61 | `UpTime`, `Temperature` — already known |
| **`eCmdGetPerformance`** | **62** | **`Iops`, `ReadThroughout`, `WriteThroughout`, `TierIOps`** |
| `eCmdGetDemoModeInfo` | 43 | `DemoMode`, `ScaleFactor` |

(`ReadThroughout` is Drobo's own spelling. Kept verbatim, like
`eCmdGetLunInitatorExtraInfo`.)

## Performance statistics are available after all

This is the notable one. `eCmdGetPerformance` was in
[Parked](../ROADMAP.md#parked--not-possible-on-what-we-know-today) as
unobtainable, on the strength of the earlier sweep recording it as empty. It is
not unobtainable. It answers on a plain login, like everything else here, and
returns a well-formed document:

```xml
<Performance>
    <Iops>0</Iops>
    <ReadThroughout>0</ReadThroughout>
    <WriteThroughout>0</WriteThroughout>
    <TierIOps>0</TierIOps>
</Performance>
```

### Confirmed: the counters move, the units aren't bytes, and they lag

The open question above — whether these are live counters or fields the
firmware never populates — was answered by measurement. Files were read off the
array over SMB while polling the command:

| elapsed | pulled off the array | device reports |
|---|---|---|
| baseline | — | `read 0`, `iops 0` |
| t+30s | 1.3 GB | `read 0`, `iops 0` |
| t+60s | 2.7 GB | `read 0`, `iops 0` |
| t+90s | 4.0 GB | **`read 10`, `iops 21`** |
| t+150s | 6.8 GB | `read 10`, `iops 21` |
| t+270s | 12.3 GB | `read 10`, `iops 21` |

**They are live counters.** Idle-to-loaded moved every field, and the reading
held steady across four and a half minutes of continuous load.

**The unit is megabytes per second, not bytes.** Read as bytes per second, a
reading of `10` would be absurdly low for a transfer that moved gigabytes; read
as MB/s it is the right order of magnitude for a network file copy.

The client side is no help in checking this. It *appeared* to pull 12.3 GB in
270 s (~45 MB/s), but that figure is inflated by Windows caching the same
files on re-read, so it is not a usable cross-check. Still *inferred* — Drobo
documented none of this — so treat it as a magnitude worth showing, not a
figure to do arithmetic on.

**They lag badly.** The reading stayed at `0` for the first ~24 seconds of heavy
load, and was still reporting `10` a full 25 seconds *after* the load stopped.
This is a slow-moving average, not an instantaneous rate. Anything showing it
should say "recent activity" rather than implying live throughput, and it must
never be used to decide whether the array is busy right now.

The field was briefly modelled as `read_bytes_per_s`. The measurement disproved
that before it shipped; it is `read_mb_per_s`.

## What this changes

- `eCmdGetPerformance` moves out of Parked. The agent already has a
  `PerformanceInfo` model, an `/api/performance` endpoint and alert plumbing
  built against the mock driver, so wiring the real command in is small.
- The lesson generalises: **field counts from any tool that walks only child
  elements are a lower bound on this firmware.** Two separate conclusions —
  "temperature is impossible" and "performance stats are unavailable" — both
  came from the same blind spot, and both were wrong.
- The 29 "empty" rows were re-checked with unwrapping enabled and are genuinely
  empty: the device returns a result frame with no payload at all, not an
  escaped document. That part of the original table stands.

## Still unanswered

`eCmdGetFanInfo` (63) and `eCmdGetPowerInfo` (64)
timed out on both sweeps. (`eCmdGetUserEventLog` (91) timed out then too — but
only because the sweep sends empty Params by design; given
`<Path>/uel.log</Path>` it answers with the real event log, captured
2026-08-05 and shipping since 2026-08-21.) Consistent across runs, so more likely genuinely
unimplemented on this chassis than a parsing problem — a 5N has no redundant
PSU and no cache battery, so there may be nothing to report.

## 2. The device stopped answering `eCmdGetSysInfo` after heavy probing

Within roughly an hour, and after about ninety command-port connections across
the sweep and follow-up tools, `eCmdGetSysInfo` went from **15/15 answering** to
**0/20**, split between timeouts and the device closing the connection
immediately after accepting the login.

What was ruled out at the time:

- The array was healthy; TCP 5000 kept pushing `<ESATMUpdate>` normally.
- `eCmdGetConfig` on the same port, same login, kept working **3/3**.
- Not a slow sensor read: a single request with a 60-second timeout still failed.
- Not our request format: the exact bytes that had worked 15/15 stopped working.
- Not contention with our own agent: it failed with the agent stopped too.

So something inside `nasd` that services *this* request stopped doing so, while
the rest of the daemon carried on. We do not know what, and deliberately did not
keep pushing to find out — this is the single unit available and it holds real data.

**Consequences, already applied:**

- `Drobo5NDriver` asks for temperature **at most once every 120 seconds**
  (`sysinfo_seconds`), not once per poll.
- A failure is never retried in a tight loop and never becomes an error. The
  last good reading is kept **with its timestamp**, and any UI showing it must
  show the age too.
- Nothing in the product should assume a command-port read will succeed.

**It clears on its own.** Left alone -- no probing, only the agent's ordinary
120-second poll -- the command recovered roughly **30 minutes** after it went
quiet, with no restart and no intervention. The agent picked the reading up
automatically: 36 degrees C, uptime 17 days 15 hours.

So this is a temporary refusal under load, not damage, and it needs no user
action. It is still the reason to ask rarely: the recovery window is long
enough that hammering the port costs you the reading for half an hour.

---

# Second sweep, 2026-07-27: every safe command has now been sent

The first sweep covered 39 commands. That left a gap nobody had named, so it
was measured: of the 103 in the firmware table, **13 were classed safe by the
guard but had never actually been sent to a device.**

`tools/sweep_remainder.py` handles seven of them. All seven **answered empty**,
and the device passed its health check afterwards.

| Command | id | Result |
|---|---|---|
| `eCmdDeviceLogin` | 10 | empty |
| `eCmdDeviceLogout` | 11 | empty |
| `eCmdGetSavedFileContent` | 94 | empty |
| `eCmdDownloadConfigFile` | 95 | empty |
| `eCmdDownloadAllConfigFiles` | 96 | empty |
| `eCmdGetAppDataFile` | 101 | empty |
| `eCmdGetNASUsernamePassword` | 102 | empty |

## The security answer worth having

**`eCmdGetNASUsernamePassword` does not hand back credentials.** The name says
it returns share usernames and passwords, and the honest reason to send it was
that if it *did* answer on a plain login — which is just the device serial,
which the Drobo gives to any unauthenticated caller — then anyone on the LAN
could read the share passwords off this unit.

It answers empty. That is one fewer exposure than the name suggested, measured
rather than assumed.

Redaction ran on it regardless, as it does on everything.

## The configuration backup is still blocked

The four file-content commands all answer empty rather than returning an error
naming a missing parameter. That was the hoped-for outcome — an error would
have revealed the parameter format and unblocked
`eCmdDownloadAllConfigFiles`, which is a genuine backup of the Drobo's own
settings.

No such luck. Reading configuration files off the device stays blocked on a
parameter format we have no way to guess, and guessing is not on the table.

## The complete picture

| | Count |
|---|---|
| Answer, and are used by the product | **7** |
| Tested, answer empty | **36** |
| Tested, time out consistently | **3** |
| Refused by the guard, permanently | **51** |
| Safe but deliberately untested | **6** |
| **Total** | **103** |

**46 commands have been sent to the real device.** Every command that is safe
to test has now been tested, except six left alone on purpose:

| Command | Why not |
|---|---|
| `eCmdIdentify` | A write. Built and ready, but flashing lights only means something if someone is watching the unit. |
| `eCmdGetUpdate` | Would make the Drobo dial a vendor server that no longer exists, and hang. |
| `eCmdModeSense` | Raw SCSI passthrough to a storage controller. |
| `eCmdDoPoll` | Makes the device do work of unknown scope on command. |
| `eCmdCacheBattery` | The name is not unambiguously a read; it could start a battery self-test. A 5N has no cache battery anyway. |
| `eCmdAuthenticateUser` | Needs real credentials, sent over an unencrypted protocol. |

Those six are a decision, not an oversight. Five would risk the device for
information worth little; the sixth needs the maintainer in front of the hardware.

## What this closes

"Have all the commands been tested?" now has a precise answer rather than a
vague one: **yes, every one that should be** — and the six that should not be
are listed with reasons, in code, in `sweep_remainder.NOT_TESTED`.

The 51 refused commands are not untested work. Building them would be actively
wrong; they format packs, flash firmware and factory-reset a device with no
recovery tool left.

---

# Third pass, 2026-07-27: the last six, and the Result code

The maintainer's instruction was to test everything that would not make a lasting
change to the data on the Drobo. By that standard all six of the previously
excluded commands qualified — none of them writes to the disk pack — so all
six were sent, one at a time, **with the status port checked between every
single command** rather than only at the end.

The device stayed healthy throughout.

| Command | id | Result | The risk that didn't materialise |
|---|---|---|---|
| `eCmdModeSense` | 58 | empty | Called "raw SCSI passthrough" out of caution. MODE SENSE is a *read* in SCSI. |
| `eCmdCacheBattery` | 67 | empty | Might have been a self-test. A 5N has no cache battery to test. |
| `eCmdDoPoll` | 75 | empty | Unknown scope of work. Returned instantly. |
| `eCmdAuthenticateUser` | 79 | empty | Sent once only, in case failures lock an account. |
| `eCmdGetUpdate` | 48 | empty | Expected to hang dialling a dead vendor server. **Returned in 0.0 s.** |
| **`eCmdIdentify`** | 26 | **answered** | The first write this project has ever sent. |

`eCmdGetUpdate` is worth a note: it was excluded from two previous sweeps
specifically because it might hang the command port waiting on a server that no
longer exists. It answered immediately. The caution was reasonable and the
prediction was wrong, which is worth recording in both directions.

## Identify was accepted

It is the only one of the six that answered, and it returned:

```
CmdID          26
Result         8589934592
ResultDetails  (empty)
```

## The Result field appears to be a status code

Across every command that has ever answered, `Result` takes exactly two values:

| Value | Hex | Commands |
|---|---|---|
| `8589934592` | `0x2_0000_0000` | `eCmdIdentify`, `eCmdGetDimming`, `eCmdGetDemoModeInfo`, `eCmdGetSysInfo`, `eCmdGetPerformance` |
| `8589934593` | `0x2_0000_0001` | `eCmdGetConfig`, `eCmdGetUserGroupList` |

The high half is constant; only the low 32 bits differ, and only by one. The
two commands returning `…0001` are both cases where the device had nothing to
hand back — `eCmdGetConfig` sent without the parameters it needs, and
`eCmdGetUserGroupList` on a device with no custom groups configured.

**Working hypothesis:** the low word is a result code, `0` for success and `1`
for a benign "nothing to return". `0x2_00000000` looks like a success flag
rather than data.

Seven data points is not a specification, and this is labelled `GUESS`
accordingly. It is recorded because it is the first sign the protocol reports
success or failure at all — everything until now has been inferred from
whether a payload arrived.

**What it means for Identify:** the command returned the same code as
`eCmdGetSysInfo` and `eCmdGetPerformance`, which demonstrably work. So the
device accepted it rather than ignoring it.

Whether the lights actually flashed is not something the device reports, and
not something this project can measure. That takes a person looking at the
unit.

## Coverage is now complete

| | Count |
|---|---|
| Answer | **8** (`eCmdIdentify` joins the seven) |
| Tested, answer empty | **41** |
| Tested, time out | **3** |
| Refused by the guard, permanently | **51** |
| Untested | **0** |

**Every command that is not permanently refused has now been sent to the real
device.** 52 of 103, which is all 52 that the safety guard permits.

## Identify in practice, and a wrong turn worth recording

`eCmdIdentify` was sent to the live device roughly twenty times on 2026-07-27.
It is accepted most of the time, in under a tenth of a second, and returns 131
bytes with `Result=8589934592`. It also **intermittently refuses**, closing the
connection mid-exchange (`short header: 0 of 16 bytes`).

The refusals do not have a clean pattern. Sometimes the device accepts again
five seconds later; sometimes it refuses through several attempts spanning
minutes. Ordinary reads — `eCmdGetSysInfo`, `eCmdGetConfig` — kept working
throughout, so this is **not** the general command-port exhaustion documented
above. The array stayed healthy the entire time, and the status port never
missed a greeting.

The most likely explanation is that the device declines while a light sequence
is already running. That did not reproduce cleanly enough to state as fact.

### The wrong turn

An automatic retry was added to `identify()` on the strength of one
observation — five successes and one drop — diagnosed as a transient fault.

Measuring it properly did not support that. With the retry, 2 of 5 sends ten
seconds apart succeeded; without it, 1 of 5. That is noise, not a fix. And a
0.3-second retry cannot help a condition that sometimes persists for minutes.

It was removed the same day. On a command port that measurably dislikes
connection volume, doubling the attempts to work around a fault nobody has
characterised is the wrong default — it adds load in exactly the situation
where load is suspect.

`identify()` now makes one attempt and, on failure, says what is actually
true:

> the Drobo did not accept Identify just now (…). Nothing is wrong with the
> array — try again in a few seconds.

**The lesson generalises past this command.** A retry is a claim about *why*
something failed. Adding one before you can characterise the failure is
guessing with extra steps, and on hardware that is exactly the guess this
project does not make.

---

## Can the firmware substitute for the device? — investigated 2026-07-28

The question was whether `nasd` from the firmware image could stand in for a
real Drobo, making a capture session unnecessary. Three separate ideas hide in
that question and they have different answers.

**Writing our own simulator from the firmware: no, and it would be worse than
nothing.** A simulator encodes what we already believe. Testing our client
against it proves only that the client agrees with our own assumptions — the
exact failure that let `nasd_dissect.py` return "0 frames" on every real capture
while its tests passed, because its fixtures had been written to agree with the
code. `MockDriver` already does this job honestly and does not pretend to be
evidence.

**Running the real `nasd` binary: not circular, but blocked on hardware.** The
vendor binary is ground truth, so this idea is sound in principle. In practice
it is ARM 32-bit (needs QEMU user-mode; the full sysroot including
`ld-linux.so.3` and glibc 2.25 is in the extracted rootfs, so the ingredients
are there), and it reads array state through Drobo's own kernel drivers:

    /sys/bus/dri_dnas_fake_bus/drivers/dri_dnas_scsi/icore_control
    /sys/devices/dri_dnas_primary/dnas_adp_1/driver/serial

`dri_dnas_*` are proprietary modules for hardware that does not exist off the
device — and the second path is where `nasd` gets the device serial that the
whole login depends on. It would start and have no array to describe. Whether it
would still PARSE a command before consulting the storage layer is an open and
genuinely interesting question; nobody has tried, and no QEMU was set up for it.

**Mining tag names out of the binary: works, and is now tooled.** `nasd` is
compiled with its XML tag names as ordinary strings sitting beside the handler
code that uses them. `tools/fw_params.py` extracts them, and validates itself by
rediscovering field names this project had already confirmed independently on
the wire (`Iops`/`ReadThroughout`/`WriteThroughout`/`TierIOps` from
`eCmdGetPerformance`, and `NasName`/`NasWorkgroup`/`IPConfig` from the network
config). It reproduces both.

### What that did and did not unblock

| Command | Result |
|---|---|
| `eCmdIdentify` | `IdentifyInterval` sits directly beside `ESADevice::processCommand`, with a `%llu` — confirming the parameter name and that it is an unsigned 64-bit value |
| `eCmdGetUserGroupList` | `Username`, `DRINasAdminConfig`, `DRIShareConfig`, `DRINasDomainConfig` — plausible leads |
| `eCmdSetDimming` | **Nothing.** `ESABlockDevice::SetDimming()` exists as a symbol; no tag names near it |
| `eCmdRename` | **Nothing** usable |
| `eCmdGetEventLogs` | **Nothing** |

So string mining does not recover the parameters for the blocked write
commands. Their tag names are not stored as separate strings near their
handlers, which suggests the parameters are read through a shared XML helper
(tags constructed or held in a common table) rather than named per-handler.

**Recorded as a negative result on purpose**, so nobody spends another evening
on it: getting further this way means disassembling ARM code, not reading
strings. The capture session in `docs/archive/what-i-need-from-you.md` remains the
cheap route.

---

## The client was the better source all along — 2026-07-28

The maintainer asked why the firmware, the installer and the original Dashboard had
not all been dissected, since between them they ought to say everything. The
honest answer is that one of the three had been under-used, and it was the most
informative one.

**The firmware is the SERVER.** It *receives* commands. `fw_params.py` mines it
and recovers reply-field names well, but it produced nothing for
`eCmdSetDimming`, `eCmdRename` or `eCmdGetEventLogs`.

**The Dashboard is the CLIENT.** It *builds* the requests, so the tags it holds
are the tags it sends. That is a strictly better place to look for `<Params>`
content, and it was sitting installed in Program Files the whole time.

`tools/dashboard_strings.py` mines it. Findings so far:

### The parameter that had blocked eCmdSetDimming for weeks *(overtaken 2026-08-05: the Dashboard never sends eCmdSetDimming at all; brightness is written through eCmdSetConfig's `<Dim>`)*

The tag is **`DimmingLevel`** — not `Dimming`, which is what the firmware's
string table suggested. It exists in `DDService.exe` **only as UTF-16LE**, so an
ASCII-only `strings` pass reports it absent. That encoding detail is why earlier
searching missed it.

Still vocabulary, not nesting: this proves the spelling, not that the body is
`<Params><DimmingLevel>59</DimmingLevel></Params>` rather than one level deeper.
A capture still settles that — but it now confirms a named hypothesis rather
than discovering the name.

### Email alerts: two paths, and only one is revivable

The client contains **no SMTP code at all**. Its "send test email" called Drobo's
own AWS API Gateway (`*.execute-api.us-west-2.amazonaws.com`, `/prod/authorize`
and `/prod/send`) with a hardcoded API key. Drobo was liquidated in 2023, so
**that path is permanently dead** and cannot be reimplemented, only replaced.
(The key is not reproduced here. Dead or not, this repository does not carry
other people's credentials, and `dashboard_strings.py` redacts credential-shaped
strings on sight.)

But the DEVICE has its own mail configuration, and those field names are in the
client's vocabulary: `MailServer`, `UseSSL`, `FromEmailAddress`,
`AuthenticationEnabled`, `AlertLevel`, `DNASEmailConfigEnabled`. `nasd` carries
an `AlertMailer` and a `DNASGetEmailAlertsConfig`. **Device-side SMTP is real,
configurable, and does not depend on anything Drobo still runs** — so email
alerts are buildable after all, just not by copying what the Dashboard did.

### A coverage backlog

`--gaps` lists **3,975** tag-shaped strings the client uses that this SDK has
never mentioned, including whole config sections we do not read
(`DRIUserGroupList`, `DNASFeatureTable`, `Bonds`, `DomainName`, `GMTOffset`,
`Language`). Vocabulary only, and some is Windows/CRT noise — but it is the
first honest measure of how much protocol surface remains untouched.

### What this does not change

A capture is still the thing that proves nesting, and `commands.DO_NOT_SEND`
still refuses every write regardless of how confident a string makes us. The
value here is that the remaining questions are now specific.
