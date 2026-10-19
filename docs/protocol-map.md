# Drobo protocol map

The reverse-engineered reference for how the Drobo communicates.

Rule for this file: **record what you actually observed, and mark anything
you're inferring.**

| Mark | Meaning |
|---|---|
| `FIRMWARE` | Read directly out of Drobo 5N firmware 4.2.1. Not a guess — but it describes what the *device* does, and doesn't prove what Dashboard actually sends. |
| `CONFIRMED` | Seen on the wire in a packet capture. |
| `GUESS` | Inferred. Treat as a lead, not a fact. |

> **Status: CONFIRMED against a live Drobo 5N (firmware 4.3.1) on 2026-07-24.**
> On connect, nasd (TCP 5000) pushes a complete `<ESATMUpdate>` status document
> — unprompted, no login — wrapped in a `DRINASD\0` frame. That greeting alone
> supplies device identity, firmware, per-slot disk state and full capacity, so
> `Drobo5NDriver` reads it and the agent now serves real data. Implemented in
> `sdk/drobo_nasd/esatm.py`; regression-tested in `sdk/tests/test_esatm.py`.
>
> Confirmed frame layout (read straight off the wire):
> ```
>   0   8   signature  "DRINASD\0"
>   8   1   major version   (0x01)
>   9   1   minor/type      (0x01)
>   10  2   flags/reserved  (0x0000)
>   12  4   big-endian uint32 payload length (includes the trailing NUL)
>   16  N   UTF-8 XML, NUL-terminated
> ```
>
> Still open: cache battery / fan / PSU — these need separate nasd
> *commands* over the command channel. **That channel's request framing is no
> longer unknown** — see "THE COMMAND PROTOCOL — SOLVED" below — but these
> particular fields still come back empty (battery) or time out (fan, PSU)
> once asked. Temperature and performance are reachable over that same
> channel and both are wired into the driver (performance since 2026-07-26).
> The event log left this list on 2026-08-21: it is fetched with
> `eCmdGetUserEventLog` (91) and `<Path>/uel.log</Path>`, though the reply's
> per-line format is still undecoded.
> See [Still open](#still-open) for what's genuinely unresolved.

### Two hardware quirks worth knowing `CONFIRMED`

Both were found by reading a live 5N and are handled in `esatm.py`.

**1. Per-drive temperature is not reported.** `<mTemperature>` is present for
every slot and is `0` on all of them — including every healthy drive. A sweep
of the whole document found no other field carrying a plausible temperature
(the only 20–60 integers are `RotationalSpeed` and `DNASFeatureTable`, neither of
which is a temperature). So this is the device's own behaviour, not a parsing
gap.

Handled by reading `0` as **unknown** rather than a literal 0 °C — important,
because the alert rules would otherwise treat a healthy array as freezing.

**Answered 2026-07-26.** A dedicated command does expose a real temperature, and
it needed no new framing: `eCmdGetSysInfo` on the command channel returns a
chassis reading (measured 36 °C, 15 calls out of 15) and the device's uptime.
It is one number for the whole box, not per drive — so `mTemperature` being `0`
per slot is exactly right, and this is the temperature the product shows. The
agent alerts on it (warning at 50 °C, critical at 60 °C).

Worth knowing why it looked impossible for so long: the reply nests an
**entity-escaped XML document** inside `<ResultDetails>`, so code searching for
a literal `<Temperature>` element found nothing and recorded the command as
unanswered. That, not a missing capability, produced the earlier "answers about
one time in six" figure. See `sdk/drobo_nasd/sysinfo.py`.

**2. A 5-bay unit reports six slots.** `<mSlotCountExp>` is `6` and the greeting
carries containers `n0`…`n5`. Slot `n5` is a permanent placeholder —
`mDiskState=0`, `mPhysicalCapacity=0`, empty `mMake`. Left as-is it shows up as a
phantom "bay 6" in the UI.

`_trim_phantom_slots()` drops it, deliberately conservatively: **only trailing,
only empty, and only when the model's real bay count is known.** A slot that
holds a disk is never hidden — over-reporting a bay is cosmetic, hiding a real
drive would not be.

---

## Test rig

| | |
|---|---|
| Drobo model | **Drobo 5N** (confirmed 2026-07-23) |
| Connection | Ethernet |
| SoC / RAM | Marvell Armada XP MV78460 (ARMv7 quad 1.2 GHz) / 2 GB DDR3 |
| Kernel | **Linux-3.2.96-3** `FIRMWARE` (uImage name in 4.2.1; earlier community figures citing 3.2.58 are from an older release) |
| SMB dialect | SMBv2 _(confirm with `Get-SmbConnection` after mapping a drive)_ |
| Firmware analysed | **4.2.1**, build `0x7bbdaf`, `release.Drobo5N.4-2-1.tdf` |
| Firmware on the unit | _(fill in — Dashboard's status page)_ |
| Drobo IP address | _(fill in)_ |
| Dashboard version | _(fill in — final Windows release was 3.5.0)_ |
| Disk pack | _(fill in)_ |

---

## Architecture

The 5N runs **two operating systems side by side** `FIRMWARE`:

```
   Dashboard (Windows)
        |
        |  TCP 5000, XML
        v
   +--------------------------------------------------+
   |  Linux 3.2.96 (ARM)          <- LXKI + LXFS      |
   |    /sbin/nasd    the daemon Dashboard talks to   |
   |    samba, netatalk, avahi, monit                 |
   +--------------------------------------------------+
        |  ioctl + "icore" intercore messages
        v
   +--------------------------------------------------+
   |  ESA / VxWorks side          <- VXI2 + RTPI      |
   |    BeyondRAID, the disk pack, drive health       |
   +--------------------------------------------------+
```

This matters: **health and capacity data does not originate in Linux.** `nasd`
asks the storage side for it across an intercore bridge and relays the answer.
Intercore message names seen: `NET_INFO`, `FW_STATUS`, `FS_MIGRATION`,
`PACK_READ_ONLY`, `ISCSI_ENABLE`, `LX_CLEARDISK`, `LX_CLEARFLASH`,
`LX_SHUTDOWN`, `UNEXPECTED_REBOOT`. There is also a text bridge —
`esa vxcmd <command ...>` — and a `vxcmdCommandHandler(int, char**, ostream&)`
symbol, reachable through the `eCmdRunESACommand` command.

---

## Transport

| Question | Answer | Confidence |
|---|---|---|
| Discovery mechanism | mDNS / Bonjour, service type **`_nasd._tcp`** | `FIRMWARE` |
| Port | **TCP 5000** | `FIRMWARE` |
| Daemon | `/sbin/nasd` — 1.5 MB stripped ARM ELF, dynamically linked | `FIRMWARE` |
| Encrypted | No TLS anywhere in the daemon. There is a `UCrypto` class with `DecodeAndDecrypt`, used for stored passwords, not transport. | `FIRMWARE` |
| Payload format | **XML** — the daemon carries a full XML DOM (`UXMLDocument`, `UXMLNode`) | `FIRMWARE` |
| Who speaks first | **The device.** An unprompted `<ESATMUpdate>` push the instant a client connects; no login. Captured 2026-07-24/25 | `CAPTURE` |
| Persistent or per-command | **Per burst.** The device sends one or two documents then goes silent; a held socket gets nothing more, so the driver reconnects to read again | `CAPTURE` |
| Idle poll interval | **Dashboard sends nothing** to get status — it is a push model (captured 2026-07-25). `eCmdDoPoll` exists in the firmware but was never seen used | `CAPTURE` |

Source: `/etc/avahi/services/nasd.service` in the firmware —

```xml
<service-group>
  <name replace-wildcards="yes">%h</name>
  <service>
    <type>_nasd._tcp</type>
    <port>5000</port>
  </service>
</service-group>
```

Corroborated by `/usr/local/sbin/nasd.sh`, whose readiness check is literally
`lsof -i :5000`.

---

## Connection model

> **Reconciled with the live device (2026-07-24):** reading is easier than the
> firmware strings below suggest. The device *volunteers* its full status in a
> `DRINASD\0` greeting the instant you connect — **no login, no session** — which
> is what the read-only driver uses. The login/session machinery below governs
> *sending commands*.
>
> **Superseded 2026-07-25 — this section is the FIRMWARE-string guess, made
> before the command channel was captured. The guess was wrong in the
> particulars.** The real command channel is a *separate port* (TCP 5001, not
> 5000) with a *different* signature (`DRINETTM`, not `DRINASD`/`DRINASD4` as
> guessed below) and a login packet that turned out to be far simpler than
> this section expected: no per-connection secret, just the device's own
> serial sent twice. See "THE COMMAND PROTOCOL — SOLVED" further down for the
> confirmed shape. Kept below as the historical record of the guess, not as
> current guidance.

`FIRMWARE` — for the *command* path this is **not** a simple "connect and send
XML" protocol. There is a binary header with a signature, a login exchange, and
a session that must exist before commands are accepted. Recovered from the
daemon's own error messages:

```
Command Conn login(from %s): did not receive complete header.
Command Conn login(from %s): got bad signature.
Command Conn login(from %s): did not receive complete login packet.
Command Conn login(from %s): cannot send login response (error %d).
Command Conn login(from %s): existing thread found.
NetTMCmds(thread %p/from %s): can't get a complete header. Size %d, Received %d.
NetTMCmds(from %s): %s/%s mismatched esaID wrong connection
SledCommandListener::%s: No session yet so refusing command connection from %s
SledCommandListener::%s: Got a command connection from %s!
SledNetAgent::%s: Unable to allocate session for %s
```

Read together, the sequence is:

```
1.  discovery            SledDiscoveryAgent / SledDiscoveryListener
                         signatures "DRINASD" and "DRINASD4"
2.  connect              SledNetAgent accepts, allocates a session
3.  command connection   SledCommandListener -- REFUSED if no session exists
4.  header + login       fixed-size header carrying a signature,
                         then a login packet; daemon sends a login response
5.  commands             each preceded by a header that declares a size
                         ("Size %d, Received %d"), payload is XML
```

Key consequences:

- **A naive "open socket, send XML" probe will fail.** It will be rejected at
  step 3 or 4 without ever reaching a command.
- **Every command carries an `esaID`** identifying the target device; a
  mismatch is explicitly rejected as "wrong connection".
- **Both the login and the command path are length-framed**, since the daemon
  can complain about receiving fewer bytes than the header declared.

### Signatures

`DRINASD` and `DRINASD4` `FIRMWARE` — both appear right next to the
`SledDiscoveryAgent` code, alongside the device serial path
(`/sys/devices/dri_dnas_primary/dnas_adp_1/driver/serial`) and a sample serial
`DRX120301700000`. "DRI" is Data Robotics Inc; the trailing `4` is most likely
a protocol version. `GUESS`: `DRINASD4` is the signature the header must carry
and `DRINASD` is the older one.

**Was unknown at the time; solved 2026-07-25 for the command channel.** The
byte layout above (`DRINASD`/`DRINASD4`) was a guess from firmware strings
and turned out not to match what's actually on the wire for commands — see
"THE COMMAND PROTOCOL — SOLVED" below for the confirmed `DRINETTM` header and
220-byte login. What's genuinely still unknown: the UDP discovery port's
packet shape (see [Genuinely still unknown](#genuinely-still-unknown)).

```
(paste an annotated hexdump of a complete request here)
```

## Message envelope

`FIRMWARE` — the XML vocabulary, recovered from adjacent string clusters:

| Element | Role |
|---|---|
| `DRINETTM` | Root element of a command message. "DRI NET TM" — the network Task Manager. |
| `ESAID` | Identifies the target device. Must match the connection. |
| `Params` | Parameter container |
| `Result`, `ResultDetails` | Response payload |
| `<Error>0</Error>` / `<Error>1</Error>` | Status. Emitted as a literal string, not built through the DOM — so it appears verbatim on the wire. |

Config documents use their own roots: `DRINASConfig`, `DRINasNetworkConfig`,
`DRINasAdminConfig`, `DRINasDomainConfig`, `DRIShareConfig`,
`DRINasDroboAppsConfig`, `DRINasEmailAlertConfig`, with sections named
`General`, `Network`, `Admin`.

The declaration emitted is exactly `<?xml version="1.0"?>`. There is **no XML
library import** — the parser is hand-written (`UXMLDocument`, `UXMLNode`),
which matters because hand-written parsers are usually strict about the exact
shape they expect.

Binary payloads inside XML are **base64** (`CommandThread::GetDiagsFile: done
with base64 encoding`, `FirmwareDownload: failed to do base64 decoding`).

So a command message is probably shaped like:

```xml
<?xml version="1.0"?>
<DRINETTM>
  <ESAID>...</ESAID>
  <Command>85</Command>      <!-- or the name; see the ID table below -->
  <Params>...</Params>
</DRINETTM>
```

`GUESS` on the child element names other than `ESAID` and `Params`.

---

## Authentication

| Question | Answer | Confidence |
|---|---|---|
| Login step exists | Yes — `eCmdAuthenticateUser`, `eCmdDeviceLogin`, `eCmdDeviceLogout`, `processCmdLoginAdmin` | `FIRMWARE` |
| Backed by | PAM — `/etc/pam.d/nasd` exists | `FIRMWARE` |
| Password handling | `UCrypto::DecodeAndDecrypt`; `DNASConfig::DecodeAndReEncode*Password` for admin, domain, email and share passwords | `FIRMWARE` |
| Sent in clear over TCP 5000 | Probably — no transport crypto found | `GUESS` |
| Session token / expiry | **None.** The command-channel login is the device serial, twice, in a 220-byte packet, answered by an empty `0x87`; nothing else is required and nothing expires (captured 2026-07-25) | `CAPTURE` |

---

## Commands

The command enum is `TMCmd::ECommands`, members named `eCmd*`. **103 carry a
confirmed numeric id** — that's the length of the pointer table below, and every
one of the 103 `eCmd*` strings in the binary appears in it exactly once. A
handful of other `eCmd*`-style names show up elsewhere in the binary's strings
(`eCmdGetiSCSIAdmin`, `eCmdSetiSCSIAdmin`, `eCmdIsSafeToShutdown`, and
`eCmdRouter`, which is mentioned in the escape-hatch prose below) but are **not**
in the numeric table, so they have no confirmed id. Treat 103 as the
authoritative count; earlier drafts of this file said "106", which conflated the
table with those loose strings. These are the daemon's internal identifiers; the
wire encoding of a command was captured 2026-07-25: a NUL-terminated
`<TMCmd><CmdID>N</CmdID><Params>...</Params><ESAID>...</ESAID></TMCmd>` inside
a `DRINETTM` frame, with the numeric `CmdID` matching this table exactly.

### Numeric command IDs

`FIRMWARE` — `nasd` carries a table of 103 `char*` pointers in `.data.rel.ro`
at virtual address `0x00191ccc`, one per command, in enum order. The array
index **is** the command's numeric value. Recovered with
`tools/fwtool.py` plus an ELF walk; the table is contiguous and complete
(all 103 `eCmd*` strings in the binary appear in it exactly once).

`GUESS`: that the enum starts at 0. The *order* is certain; a constant offset
is possible.

<details>
<summary>Full ID table (103 commands)</summary>

| ID | Command |
|---|---|
| 0 | `eCmdSetAdmin` |
| 1 | `eCmdGetAdmin` |
| 2 | `eCmdCreateLUN` |
| 3 | `eCmdDeleteLUN` |
| 4 | `eCmdRenameLUN` |
| 5 | `eCmdGetNextUniqueLUNID` |
| 6 | `eCmdSetCHAP` |
| 7 | `eCmdGetVersion` |
| 8 | `eCmdSetDimming` |
| 9 | `eCmdGetDimming` |
| 10 | `eCmdDeviceLogin` |
| 11 | `eCmdDeviceLogout` |
| 12 | `eCmdMount` |
| 13 | `eCmdUnmount` |
| 14 | `eCmdForceUpdate` |
| 15 | `eCmdGetDiskPackId` |
| 16 | `eCmdRepair` |
| 17 | `eCmdSetFlashConfig` |
| 18 | `eCmdSendTestEmail` |
| 19 | `eCmdSendDashboardTestEmail` |
| 20 | `eCmdGetKernelInterfaceInfo` |
| 21 | `eCmdRestart` |
| 22 | `eCmdStandby` |
| 23 | `eCmdShutdown` |
| 24 | `eCmdReset` |
| 25 | `eCmdRename` |
| 26 | `eCmdIdentify` |
| 27 | `eCmdInstallFirmware` |
| 28 | `eCmdRevertFirmware` |
| 29 | `eCmdUploadDiags` |
| 30 | `eCmdGetConfig` |
| 31 | `eCmdSetConfig` |
| 32 | `eCmdGetFirmwareInfo` |
| 33 | `eCmdFormatDevice` |
| 34 | `eCmdFormatLUN` |
| 35 | `eCmdGetDroboSyncSettings` |
| 36 | `eCmdSetDroboSyncSettings` |
| 37 | `eCmdGetDroboSyncSummaryLog` |
| 38 | `eCmdGetDroboSyncDetailedLog` |
| 39 | `eCmdGetDroboSyncLog` |
| 40 | `eCmdSyncNow` |
| 41 | `eCmdStopSync` |
| 42 | `eCmdGetReservedDriveLetters` |
| 43 | `eCmdGetDemoModeInfo` |
| 44 | `eCmdSetDemoModeInfo` |
| 45 | `eCmdUpdateUserEmailSetting` |
| 46 | `eCmdGetUserEmailSettings` |
| 47 | `eCmdGetDevices` |
| 48 | `eCmdGetUpdate` |
| 49 | `eCmdInsertDrive` |
| 50 | `eCmdRemoveDrive` |
| 51 | `eCmdRunESACommand` |
| 52 | `eCmdSendTMCommand` |
| 53 | `eCmdEnableAutoDiscovery` |
| 54 | `eCmdIsAutoDiscoveryEnabled` |
| 55 | `eCmdAddManualDiscoveryIP` |
| 56 | `eCmdRemoveManualDiscoveryIP` |
| 57 | `eCmdGetManualDiscoveryIPList` |
| 58 | `eCmdModeSense` |
| 59 | `eCmdIsSCSIConnAvailable` |
| 60 | `eCmdAddVolume` |
| 61 | `eCmdGetSysInfo` |
| 62 | `eCmdGetPerformance` |
| 63 | `eCmdGetFanInfo` |
| 64 | `eCmdGetPowerInfo` |
| 65 | `eCmdGetControllerCard` |
| 66 | `eCmdGetExpanderCard` |
| 67 | `eCmdCacheBattery` |
| 68 | `eCmdForceSingleInitiator` |
| 69 | `eCmdGetOSInfo` |
| 70 | `eCmdReadHostBuffer` |
| 71 | `eCmdWriteHostBuffer` |
| 72 | `eCmdGetTMDiagFilePath` |
| 73 | `eCmdForceRepair` |
| 74 | `eCmdGetLunInitatorExtraInfo` |
| 75 | `eCmdDoPoll` |
| 76 | `eCmdToggleNightMode` |
| 77 | `eCmdGetDroboAppsStatus` |
| 78 | `eCmdDroboAppsAction` |
| 79 | `eCmdAuthenticateUser` |
| 80 | `eCmdGetAdminConfig` |
| 81 | `eCmdGetSharesConfig` |
| 82 | `eCmdSetTime` |
| 83 | `eCmdGetEventLogs` |
| 84 | `eCmdGetSessionIPAddress` |
| 85 | `eCmdGetAllDevicesStatusXML` |
| 86 | `eCmdUploadFileToSupport` |
| 87 | `eCmdCancelUploadToSupport` |
| 88 | `eCmdGetRegisteredEmail` |
| 89 | `eCmdRunCommand` |
| 90 | `eCmdGetUserGroupList` |
| 91 | `eCmdGetUserEventLog` |
| 92 | `eCmdUpdateUserGroupList` |
| 93 | `eCmdGetDeviceNamesToShutdown` |
| 94 | `eCmdGetSavedFileContent` |
| 95 | `eCmdDownloadConfigFile` |
| 96 | `eCmdDownloadAllConfigFiles` |
| 97 | `eCmdGetDASDeviceNamesWithMountedVolumes` |
| 98 | `eCmdGetDeviceMountedVolumesCount` |
| 99 | `eCmdPostUserEvent` |
| 100 | `eCmdDownloadFTPFileToSharedPath` |
| 101 | `eCmdGetAppDataFile` |
| 102 | `eCmdGetNASUsernamePassword` |

</details>

### Status and inventory

| Command | Likely purpose |
|---|---|
| `eCmdGetAllDevicesStatusXML` | **The main status poll.** Almost certainly what Dashboard's front page is built from. Start here. |
| `eCmdDoPoll` | Periodic poll tick |
| `eCmdGetDevices` | Enumerate attached Drobos |
| `eCmdGetSysInfo` | System information |
| `eCmdGetConfig` / `eCmdSetConfig` | Generic config get/set |
| `eCmdGetVersion` | Protocol or software version |
| `eCmdGetOSInfo` | OS information |
| `eCmdGetDiskPackId` | Disk pack identity |
| `eCmdGetPerformance` | Throughput statistics |

### Health and hardware

| Command | Likely purpose |
|---|---|
| `eCmdGetFanInfo` | Fan status |
| `eCmdGetPowerInfo` | PSU status |
| `eCmdCacheBattery` | Battery/cache health |
| `eCmdGetControllerCard` / `eCmdGetExpanderCard` | Controller and expander info |
| `eCmdGetKernelInterfaceInfo` | Kernel interface |
| `eCmdModeSense` | SCSI MODE SENSE passthrough |
| `eCmdInsertDrive` / `eCmdRemoveDrive` | Drive events |
| `eCmdIdentify` | Blink the lights |
| `eCmdGetDimming` / `eCmdSetDimming` / `eCmdToggleNightMode` | LED brightness |

### Volumes and the pack

`eCmdAddVolume`, `eCmdMount`, `eCmdUnmount`, `eCmdFormatDevice`, `eCmdRename`,
`eCmdRepair`, `eCmdForceRepair`, `eCmdGetReservedDriveLetters`,
`eCmdGetDeviceMountedVolumesCount`, `eCmdGetDASDeviceNamesWithMountedVolumes`

### iSCSI / LUNs

`eCmdCreateLUN`, `eCmdDeleteLUN`, `eCmdFormatLUN`, `eCmdRenameLUN`,
`eCmdGetNextUniqueLUNID`, `eCmdGetLunInitatorExtraInfo` *(sic — Drobo's typo)*,
`eCmdSetCHAP`, `eCmdForceSingleInitiator`, `eCmdGetiSCSIAdmin`,
`eCmdSetiSCSIAdmin`, `eCmdIsSCSIConnAvailable`

### Shares, users, network

`eCmdGetSharesConfig`, `eCmdGetAdminConfig`, `eCmdSetAdmin`, `eCmdGetAdmin`,
`eCmdGetUserGroupList`, `eCmdUpdateUserGroupList`, `eCmdGetNASUsernamePassword`,
`eCmdGetSessionIPAddress`

Handled internally by `DNASConfig`: `DNASGetNetworkConfig`,
`DNASGetShareConfig`, `DNASGetAdminConfig`, `DNASGetDomainConfig`,
`DNASGetAppsConfig`, `DNASGetEmailAlertsConfig`, `DNASSetAllConfig`,
`DNASClearConfig`, `DNASAuthenticate`, `DNASGetUserGroupList`

### DroboApps

`eCmdGetDroboAppsStatus`, `eCmdDroboAppsAction`, `eCmdGetAppDataFile`

Referenced listing files: `droboapps_listing_SDK.%s.xml`,
`droboapps/%s/droboapps_listing_2.5.2.xml`

### Firmware

`eCmdGetFirmwareInfo`, `eCmdInstallFirmware`, `eCmdRevertFirmware`,
`eCmdGetUpdate`, `eCmdForceUpdate`

Upload is chunked and CRC-checked — `Firmware::IdleHandler upgrade started
chunk 0x%x`, `Firmware::IdleHandler: Wrong Seq %d`, `CRC Error(Received %x,
Calculated %x) Seq %d`. **Out of scope for this project** (see
`docs/archive/reality-check.md` §1); documented only so it is recognised and avoided.

### Power

`eCmdShutdown`, `eCmdRestart`, `eCmdReset`, `eCmdStandby`,
`eCmdGetDeviceNamesToShutdown`, `eCmdIsSafeToShutdown` *(via
`ESABlockDevice::IsSafeToShutdown`)*

### Logs, diagnostics, email

`eCmdGetEventLogs`, `eCmdGetUserEventLog`, `eCmdPostUserEvent`,
`eCmdUploadDiags`, `eCmdUploadFileToSupport`, `eCmdCancelUploadToSupport`,
`eCmdGetTMDiagFilePath`, `eCmdGetSavedFileContent`,
`eCmdDownloadConfigFile`, `eCmdDownloadAllConfigFiles`,
`eCmdSendTestEmail`, `eCmdSendDashboardTestEmail`, `eCmdGetUserEmailSettings`,
`eCmdUpdateUserEmailSetting`, `eCmdGetRegisteredEmail`

### DroboSync

`eCmdGetDroboSyncSettings`, `eCmdSetDroboSyncSettings`, `eCmdSyncNow`,
`eCmdStopSync`, `eCmdGetDroboSyncLog`, `eCmdGetDroboSyncDetailedLog`,
`eCmdGetDroboSyncSummaryLog`

### Discovery

`eCmdEnableAutoDiscovery`, `eCmdIsAutoDiscoveryEnabled`,
`eCmdAddManualDiscoveryIP`, `eCmdRemoveManualDiscoveryIP`,
`eCmdGetManualDiscoveryIPList`

Manual-IP discovery exists, so **a Drobo can be reached without mDNS** — useful
if Bonjour is unreliable on modern Windows.

**Implemented client-side (`sdk/drobo_nasd/discovery.py`), without
needing any of the above commands.** Since the device *identifies itself* in
the greeting (`mESAID`, the permanent hardware serial), the agent doesn't need
`nasd`'s own auto-discovery machinery — it runs its own mDNS browse for
`_nasd._tcp`, falls back to a `.local` hostname, then a last-known IP, then
(opt-in only) a bounded subnet sweep, and treats "something answered on port
5000" as provisional until the greeting's `mESAID` matches the serial the
caller expects. A mismatch raises `IdentityMismatchError` rather than
trusting the candidate. No static IP or manual-IP command needed.

### Escape hatches

| Command | Note |
|---|---|
| `eCmdRunESACommand` | Runs a command on the VxWorks storage side |
| `eCmdRunCommand` | Runs a command |
| `eCmdSendTMCommand` | Sends a command to the task manager |
| `eCmdRouter` | Routes a command somewhere |
| `eCmdReadHostBuffer` / `eCmdWriteHostBuffer` | Raw buffer access |

> **Treat this group as dangerous.** They appear to be arbitrary command
> execution paths on a device with no security updates. The agent must never
> expose them, and nothing should be sent to them during exploration.

### Other

`eCmdGetDemoModeInfo`, `eCmdSetDemoModeInfo`, `eCmdSetTime`,
`eCmdSetFlashConfig`, `eCmdDownloadFTPFileToSharedPath`,
`eCmdDeviceLogin`, `eCmdDeviceLogout`, `eCmdAuthenticateUser`

---

## Status payload

*(Overtaken 2026-07-24: `read_snapshot()` reads the unprompted `<ESATMUpdate>`
greeting on port 5000 — the field map is below — and `eCmdGetAllDevicesStatusXML`
(85) itself answers empty under a plain login. Kept as first written.)*

`eCmdGetAllDevicesStatusXML` is named for exactly what the agent needs. Capture
one response and paste it here — this single field breakdown unblocks
`Drobo5NDriver.read_snapshot()`.

```
(paste it here)
```

| Field | Where it lives | Type | Example | Meaning | Confidence |
|---|---|---|---|---|---|
| | | | | | |

---

## Drive health and state

Not yet mapped. The values come from the ESA side via
`ESABlockDevice`/`ESABatteryInfo::parse`, so the vocabulary may differ from
anything visible in `nasd`.

---

## Capacity / BeyondRAID maths

Not yet mapped. Reconcile used / free / protected / reserved against the
capacity fields once a status payload is captured — if the parts don't add up,
a field has been misidentified.

---

## Non-Dashboard interfaces

Confirmed present in the firmware:

| Interface | Port | Notes |
|---|---|---|
| `nasd` management | **5000/tcp** | The Dashboard protocol. `_nasd._tcp` via mDNS. |
| SMB (Samba) | 445 | `/etc/samba/smb.conf`; share templates at `/etc/nasd/shares/templates/samba.inc` |
| AFP (netatalk) | 548 | `/etc/netatalk/afp.conf`; advertised via `_afpovertcp` |
| SSH | 22 | `/etc/avahi/services/ssh.service` and `sftp-ssh.service` exist — SSH is advertised, though whether the daemon runs by default is untested |
| mDNS (avahi) | 5353/udp | `/etc/avahi/avahi-daemon.conf` |
| iSCSI | 3260 | LUN commands exist; 5N is not an iSCSI model, so likely shared code |
| LDAP | — | `/etc/openldap/templates/drobo.ldif`, `/etc/nslcd.conf` — the domain-join feature |

Process supervision is `monit` (`/etc/monitrc`, `/etc/monit.d/nasd.cfg`), which
restarts `nasd` if it exceeds 30% CPU for 21 cycles.

---

---

## ESATMUpdate greeting — confirmed field map

`CONFIRMED (live 5N, fw 4.3.1, 2026-07-24)`. Root element `<ESATMUpdate>`.
Sanitized sample committed at `sdk/tests/fixtures/esatm-sample.xml`.

### Device level

| XML tag | Meaning | Maps to |
|---|---|---|
| `mDroboName` / `mName` | device name | `device.name` |
| `mVersion` | firmware, e.g. `4.3.1-8.126.117497` | `device.firmware` |
| `mTotalCapacityProtected` | usable bytes (single-redundancy) | `capacity.usable_bytes` |
| `mUsedCapacityProtected` | used bytes | `capacity.used_bytes` |
| `mFreeCapacityProtected` | free bytes | `capacity.free_bytes` |
| `mPhysicalCapacity` | one disk's raw size (device level) | (per-slot sum used instead) |
| `DNASStatus`, `mDiskPackStatus`, `mStatus` | status/health codes | overall state (interpretation still partial) |
| `DNASDiskPackId` | disk pack identity | — |
| `mFirmwareFeatures`, `mFirmwareFeatureStates` | capability bitmasks | — (Phase 2) |

### Per slot (repeats; one per bay, `mSlotNumber` 0-indexed)

| XML tag | Meaning |
|---|---|
| `mSlotNumber` | bay index (0-based; UI shows +1) |
| `mStatus` | `3` = populated, `128` = empty bay |
| `mDiskState` | `16` = healthy (confirmed on every healthy drive observed); other non-zero → treated as warning until a degraded disk is observed |
| `mPhysicalCapacity` | disk size in bytes (`0` when empty) |
| `mSerial` | disk serial |
| `mMake` | manufacturer, as the short vendor code the drive reports |
| `mDiskFwRev` | disk firmware revision |
| `mDiskType` | `0` = HDD |
| `mTemperature` | **reports 0 on this 5N** — not usable from the greeting |
| `mErrorCount`, `RotationalSpeed`, `SSDLifeRemaining`, `mESAID` | present, not yet mapped |

Codes not yet decoded (would need a device in each state to confirm): the exact
meaning of `mStatus`/`mStatusEx`/`DNASStatus` numeric values, and non-16
`mDiskState` values. The parser fails safe — unknown disk states surface as a
warning rather than OK.

---

## THE COMMAND PROTOCOL — SOLVED `CONFIRMED` 2026-07-25

**This was the project's last unknown. It is now fully recovered**, from a
capture of the real Dashboard clicking *Identify* (the blink button) on a live
Drobo 5N. Everything below was read off the wire.

Serials are shown as `<ESAID>` — the real one is a 15-character string like
`drbNNNNNNNNNNNN`, read from `mESAID` in the status greeting.

### Where it happens

**TCP port 5001**, not 5000. This is the `SledCommandListener` from the firmware
strings. It is silent on connect because **the client speaks first** — which is
why every earlier probe saw nothing, and why two capture sessions of pure
*viewing* never touched it. Reading status (5000) and issuing commands (5001)
are entirely separate channels.

### Frame format

Identical in shape to the status channel's, but a different magic:

```
    offset  bytes  meaning
    0       8      signature "DRINETTM"      <- note: NOT "DRINASD\0"
    8       1      message type
    9       1      version (observed 0x01)
    10      2      flags (usually 0x0000)
    12      4      big-endian uint32 payload length
    16      N      payload (XML is NUL-terminated; the NUL is inside N)
```

### Message types

**A response is the request type with the high bit set** (`type | 0x80`) —
clean and easy to match up.

| Type | Direction | Meaning | Payload |
|---|---|---|---|
| `0x07` | → device | **Login** | 220 bytes, binary (below) |
| `0x87` | ← device | Login accepted | empty |
| `0x0A` | → device | **Command** | `<TMCmd>` XML |
| `0x8A` | ← device | Command result | `<TMCmd>` XML with `<ResultDetails>` |
| `0x09` | → device | Keepalive | empty |
| `0x89` | ← device | Keepalive ack | empty |
| `0x04` / `0x84` | both | Binary exchange, purpose not yet identified | 1–178 bytes |
| `0x83` | ← device | Short binary reply | 4 bytes |

### The login packet (type `0x07`)

220 bytes, and simpler than the firmware's talk of signatures suggested — it is
just the device's own serial, twice, in two 20-byte NUL-padded fields:

```
  offset  0..14   ESAID, ASCII (15 chars)
  offset 15..19   NUL padding
  offset 20..34   ESAID again
  offset 35..219  NUL padding
```

There is **no password and no credential of any kind.** Knowing the serial —
which the device hands out unauthenticated on port 5000 to anyone who connects —
is the entire admission requirement. Worth stating plainly: on this protocol,
anything on the LAN that can read the status port can also issue commands.

The device replies `type=0x87, length=0`. That is the whole handshake.

### Commands (type `0x0A`)

```xml
<TMCmd><CmdID>26</CmdID><Params>...</Params><ESAID>...</ESAID></TMCmd>
```

NUL-terminated. `<Params>` and `<ESAID>` appear in either order. Responses echo
the `CmdID` and carry `<ResultDetails>`.

### The command IDs are CONFIRMED

Every `CmdID` seen on the wire maps exactly onto the numeric table recovered
independently from firmware — **the two sources agree completely.** That
validates the whole 103-entry enum, not just these six.

| CmdID | Name | Observed params |
|---|---|---|
| 14 | `eCmdForceUpdate` | — |
| 26 | `eCmdIdentify` | `<IdentifyInterval>900</IdentifyInterval>` ← the blink |
| 30 | `eCmdGetConfig` | `<DRIShareConfig>`, `<DRINasAdminConfig>`, `<Network>` |
| 61 | `eCmdGetSysInfo` | — |
| 77 | `eCmdGetDroboAppsStatus` | — |
| 82 | `eCmdSetTime` | `<Time>` unix seconds, `<GMTOffset>` |

### Two privilege tiers — measured `CONFIRMED` 2026-07-25, retracted 2026-07-26

**The "Tier 2 needs crypto" conclusion below does not hold. Read this note
before the table.** The raw measurements stand — they were real runs — but
the interpretation drawn from them was wrong, in the same way the 1-in-6
figure for `eCmdGetSysInfo` was wrong: `eCmdGetSysInfo`'s reply nests an
**entity-escaped XML document** inside `<ResultDetails>` instead of real XML
children, so code looking for a literal `<Temperature>` element found nothing
on most calls and logged them as failures that weren't. Measured properly
(`sdk/drobo_nasd/sysinfo.py`), it answers **15 out of 15**, no crypto
exchange involved. The 2026-07-26 sweep (`docs/command-surface.md`, 39
commands under nothing but a plain login) also got real data from
`eCmdGetPerformance`, `eCmdGetDroboAppsStatus`, `eCmdGetDemoModeInfo`,
`eCmdGetUserGroupList` and `eCmdGetDimming` — all of which this section
originally assumed were behind the crypto step. So "Tier 1" / "Tier 2" was
never a real boundary; it was mostly this project misreading its own results.
See `docs/crypto-handshake.md` for where the handshake itself — which was
real, and was captured — now stands: characterized, but its purpose is
unconfirmed.

Sending the commands ourselves revealed something the capture alone did not
make obvious: on this run, not every command answered after a plain login.
Measured against the live device, each command on its own fresh connection:

| Command | Result |
|---|---|
| `eCmdGetConfig` (30) | **5 / 5 succeeded**, 0.0–0.4 s each |
| `eCmdGetSysInfo` (61) | **1 / 6 succeeded on this run** (20 s timeouts otherwise) — later measured 15/15 once the escaped-reply parsing bug was found; see above |
| `eCmdCacheBattery` (67) | accepted, **empty body** |
| `eCmdGetEventLogs` (83) | accepted, **empty body** |
| `eCmdGetOSInfo` (69), `eCmdGetFirmwareInfo` (32) | accepted, **empty body** |
| `eCmdGetFanInfo` (63), `eCmdGetPowerInfo` (64), `eCmdGetPerformance` (62) | **no reply at all on this run** — `eCmdGetPerformance` answered with real data in the 2026-07-26 sweep; fan/PSU are still never answered as of that sweep either |

At the time, the ordering below looked like the tell — Dashboard sending
`eCmdGetConfig` **before** a binary exchange and everything else **after**
it:

```
  client  0x07  login (220 bytes, ESAID twice)
  device  0x87  accepted
  client  0x0A  CmdID 30 GetConfig  x3        <-- works with login alone
  client  0x04  1 byte: 0x10                  <-- a binary exchange begins
  device  0x84  130 bytes, binary/high-entropy    (challenge)
  client  0x84  178 bytes, base64 ASCII           (response)
  device  0x83  4 bytes: 00 00 00 00             (accepted)
  client  0x0A  CmdID 61, 26, 82, ...         <-- believed, at the time, to need the exchange above
```

The firmware does show `UCrypto::DecodeAndDecrypt` and an `<RSAKeyValue>`
string in `nasd`, so the exchange itself is real and is most likely RSA-family
(fully characterized in `docs/crypto-handshake.md`). What's retracted is only
the conclusion that it *gates* the commands in the table above — the
2026-07-26 sweep got most of them to answer without ever performing this
exchange.

Note the empty-body cases (cache battery, event log, OS info, firmware info)
are still ambiguous: they may mean "accepted but nothing to report on this
model", or something else entirely. Fan and PSU time out rather than reply at
all, which is at least as consistent with "no such sensor" as with any gate.
**No conclusion should be drawn about whether a 5N has a cache battery or fan
sensor** from the wire alone.

### What this unblocks

The fields in [Parked](../ROADMAP.md#parked--not-possible-on-what-we-know-today) — cache
battery, fan, PSU, performance, event log — were parked because the *status
push* does not carry them and no way to ask existed. **That reason is now gone.**
`eCmdCacheBattery`, `eCmdGetFanInfo`, `eCmdGetPowerInfo`, `eCmdGetPerformance`
and `eCmdGetEventLogs` all have known IDs and a working channel to send them on.

Whether a 5N actually *reports* those sensors is still unknown — that is a
hardware question, not a protocol one, and it can now be answered by asking.

**Also worth being clear:** this is the *write* path. `eCmdIdentify` is
harmless, but the same channel carries format, reset and firmware commands.
Anything built on this should keep the existing DO-NOT-SEND list enforced.

---

## Still open

> **Substantially revised 2026-07-25 after capturing the real Dashboard.**
> Most of what was listed here turned out to be unanswerable *because it does
> not happen*. See the finding directly below before planning work against it.

### What two live captures actually showed `CONFIRMED`

Drobo Dashboard 3.5.0 was installed and driven against the live 5N while
recording all traffic to and from the device (`pktmon`, filtered by IP so UDP
would be caught too). Two independent sessions, ~2.3 MB each:

| Measurement | Session 1 | Session 2 |
|---|---|---|
| Ports used with the Drobo | TCP 5000 only | TCP 5000 only |
| **Dashboard → Drobo payload** | **0 bytes** (352 pkts) | **0 bytes** (364 pkts) |
| Drobo → Dashboard payload | ~173 KB of documents | ~173 KB of documents |
| DRINASD documents | 24, all `len=7181`, `ver=1.1` | 24, all `len=7181`, `ver=1.1` |
| Document types | `<ESATMUpdate>` only | `<ESATMUpdate>` only |

**Dashboard never sends a single payload byte on TCP 5000.** Every packet from
the client is a bare TCP handshake or ACK. The device simply pushes the same
`<ESATMUpdate>` document over and over, on a fresh connection each time.

Three consequences, and they matter:

1. **There is no client→device command framing to recover on port 5000**, because
   none is used. The earlier plan — "capture Dashboard issuing commands, then
   dissect the request header" — was aimed at something that does not occur.
2. **We already receive everything the original Dashboard receives** on this
   port. Our driver is at parity with the real client, not behind it.
3. **Cache battery, fan, PSU and performance are not in this data at all.** A
   field-name sweep of the 90 tags in `<ESATMUpdate>` finds no battery, fan,
   power, PSU, voltage, RPM or thermal element — only `mTemperature`, which
   reads `0`. Those fields cannot be obtained by parsing harder; they are not
   present.

### How Dashboard actually behaves on the wire `CONFIRMED`

> **⚠️ Reading these captures: `pktmon` records each packet at MULTIPLE stack
> layers.** One capture held 2,680 frames but only **476 unique** — every packet
> appears up to 6 times, microseconds apart. Naive analysis therefore reports
> ~6× the real traffic and invents hundreds of "retransmissions" that never
> happened. **Deduplicate byte-identical frames before drawing any conclusion.**
> Corrected 2026-07-25 after an earlier pass published inflated figures.
> After deduplication: **zero genuine retransmissions** — the link is clean.


Measured from the capture, per TCP connection:

| Observation | Value |
|---|---|
| New connection opened every | **10.0 s**, metronomically |
| Documents received per connection | **1 or 2** |
| Typical hold time | most close immediately after the burst; several held ~7.5 s; one held **120 s** |
| Client bytes sent | zero, always |

Three things follow:

1. **Dashboard polls on a 10-second cadence** by opening a fresh connection each
   time. Our agent's default `poll_seconds: 10` matches it exactly — arrived at
   independently, now confirmed as the original's real behaviour.
2. **The device sends one or two copies per connection.** We read one and close,
   which loses nothing.
3. ~~A connection held for 120 seconds kept receiving pushes.~~ **Tested and
   DISPROVEN 2026-07-25.** Reading that one long-lived connection out of the
   capture, it looked as though the device would keep volunteering status on a
   held socket — which would have allowed push-driven updates instead of
   polling. Held directly against the live device four times, it does not: the
   Drobo sends its burst (1–2 frames) on connect and then goes **silent**,
   sometimes closing after ~15 s, sometimes staying open but quiet for 40 s+.

   So a held connection yields stale data, and **reconnecting is not a legacy
   habit in Dashboard — it is required.** Dashboard's 10-second reconnect is the
   correct design, and matching it is right rather than merely faithful. The
   120 s connection in the capture was almost certainly just still open when the
   recording stopped, not actively fed.

   Recorded here because it is exactly the kind of plausible inference that
   deserves a test before it becomes an architecture.

### The command path — why port 5001 had stayed silent (as of 2026-07-24)

> **Answered the next day.** Everything in this subsection and the two below
> it describes the state of things *before* the command channel was captured
> and confirmed on 2026-07-25 — kept for the record of how the question was
> narrowed down, not as a current "what's unknown" list. See "THE COMMAND
> PROTOCOL — SOLVED" above for what port 5001 actually speaks, and
> `docs/command-surface.md` for how far it's been swept since.

The firmware names two distinct listeners:

- `SledNetAgent` — accepts the connection and **allocates a session**; this is
  the 5000 status push we already consume.
- `SledCommandListener` — logs `Got a command connection from %s!` and
  `No session yet so refusing command connection from %s`. This is a **separate
  connection for commands**, and it expects the client to speak first
  (`did not receive complete header`, `got bad signature`,
  `did not receive complete login packet`).

That resolves the mystery cleanly: **port 5001 is silent because it is the write
path, and nothing we or Dashboard did during the captures was a write.** Both
capture sessions were pure viewing, so no command connection was ever opened.

Tested directly against the live device (TCP connect only, no payload sent):
5001 stays silent with no session, with a live 5000 session open, and after that
session closes. It never speaks first, in any state — consistent with a listener
waiting for a client header.

**To capture it, Dashboard must be made to *do* something**, not just display.
The safest possible action is "Identify" (flash the unit's lights, `eCmdIdentify`)
— purely cosmetic, nothing persistent, nothing touching the disk pack.

### Where those fields might still live — resolved 2026-07-25/26

- ~~**TCP 5001** is open on the device and, unlike 5000, is **silent on
  connect** — the client must speak first. Dashboard did not touch it in
  either capture. This is the most promising remaining lead and is entirely
  unexplored.~~ **Explored.** It's the command channel (`DRINETTM` framing,
  220-byte login), and `docs/command-surface.md` has since swept it with a
  39-command allowlist. Not everything it returns is useful yet (see "Two
  privilege tiers" above), but "unexplored" no longer describes it.
- **They may not exist for a 5N.** Still the live explanation for cache
  battery, fan, PSU and event log specifically — the `eCmd*` table was
  recovered from firmware shared across the whole product line, including
  business models with redundant PSUs and cache batteries. A command existing
  in the binary does not prove this chassis reports it, and asking directly
  (above) hasn't settled it either: empty replies and timeouts are consistent
  with "not present" but don't prove it.
- The UDP discovery channel (`DDService.exe` binds its own port; see below)
  was not exercised during either capture, since Dashboard already knew the
  device. Still genuinely unexplored — see below.

### Genuinely still unknown

- [ ] The UDP discovery exchange — port and packet shape (`NetUDPSocket` in the
      firmware confirms it is UDP; `DDService.exe` was observed bound to
      `:65430` on the LAN interface)
- [ ] Whether a 5N reports fan/PSU/cache-battery over *any* interface (see
      above — asking directly hasn't settled this)
- [ ] Meanings of `mStatus` (98304 device / 3 per healthy drive), `DNASStatus`
      (6), `mStatusEx`, and non-16 `mDiskState` values
- [ ] What, if anything, the tier-2 crypto handshake (`docs/crypto-handshake.md`)
      actually gates, now that several commands it was assumed to gate answer
      without it

### Fields in `<ESATMUpdate>` we did not surface at first

*(Surfaced since 2026-07-25: every field below is parsed into `DriveSlot` /
`PackHealth` and shown in both UIs. Kept as written for the record.)*

These need **no protocol work** — the data is already arriving and is simply
unparsed. This is the cheapest available win:

`mErrorCount` (per drive), `mDiskPackStatus`, `DNASStatus`, `mRelayoutCount`,
`mDoubleDegradedCnt`, `SSDLifeRemaining`, `mSledStatus`, `mSledVersion`,
`mRealTimeIntegrityChecking`, `mRedThreshold` / `mYellowThreshold` (the
device's own capacity alert levels), `mFirmwareFeatures`, `mConnectionType`,
`mManagedCapacity`, `mTotalCapacityUnprotected`.

### Tooling note

`tools/nasd_dissect.py` reported "Read 0 link-layer frames" on both captures
even though they are valid pcapng (SHB + IDB linktype 1 + 2,680 EPBs), so the
analysis above was done with a direct parser instead.

**Fixed 2026-07-26.** It read pcapng block types big-endian; the format records
its byte order per section and every capture tool on Windows writes
little-endian. The section header survived because `0x0A0D0D0A` is a
byte-palindrome, and block *lengths* were already read correctly — so the parser
walked the whole file and matched nothing, which printed as an empty capture
rather than as a failure. It now reads 2,680 frames / 17 streams from the first
capture and 3,096 / 7 from the second, matching the direct parser exactly.

Its own test suite had built pcapng fixtures big-endian — a byte order no real
tool produces — so the tests passed for as long as the tool was broken. Worth
remembering when a fixture and the code it tests were written together.

### The quick way to answer them

[`tools/nasd_probe.py`](../tools/nasd_probe.py) tries every plausible framing
against the real Drobo and records what comes back — one command instead of a
Wireshark session:

```bash
py tools/nasd_probe.py <drobo-ip> -o docs/captures/nasd-probe.json
```

It only sends read-only commands from a hard-coded allow-list, and explicitly
refuses the arbitrary-execution group. `--listen-only` connects and reads
without sending anything at all.

If it comes back empty — meaning every framing guess was wrong, or nasd wants
authentication first — fall back to `docs/archive/phase-1-capture-playbook.md`. That's
now a much smaller job too: the Wireshark filter is `tcp port 5000 and host
<drobo-ip>`, and you know to expect XML.

---

## How these findings were obtained

Static analysis of `release.Drobo5N.4-2-1.tdf`, unpacked with
[`tools/fwtool.py`](../tools/fwtool.py). Nothing was executed and nothing was
written to any Drobo.

```bash
py tools/fwtool.py info    "vendor/Drobo-5N-firmware-4.2.1/release.Drobo5N.4-2-1.tdf"
py tools/fwtool.py extract "vendor/Drobo-5N-firmware-4.2.1/release.Drobo5N.4-2-1.tdf" -o vendor/extracted
py tools/fwtool.py rootfs  vendor/extracted/fw-LXFS.bin -o vendor/extracted/rootfs
py tools/fwtool.py strings vendor/extracted/rootfs/sbin/nasd --grep "eCmd"
```

The container turned out to be a Data Robotics format tagged `TDIH`, holding
four sections — two ARM ELF images for the storage side, a Linux kernel, and a
30 MB JFFS2 root filesystem. Rebuilding the JFFS2 filesystem yielded 1,117
readable files including the Avahi service definition that names the port, the
init scripts, and `/sbin/nasd` itself. The command enum came from log format
strings inside `nasd`, which retain C++ class and method names even though the
binary is stripped.

---

## What the original Dashboard really sends — CONFIRMED 2026-08-05

A five-minute capture of Drobo Dashboard 3.5.0 driving this project's own 5N,
decoded with `tools/extract_commands.py`. 163 commands. Everything below is
CONFIRMED for the case captured, in the strict sense this document uses.

### The command channel's actual shape

    DRINETTM <8 binary header bytes> <TMCmd><CmdID>30</CmdID><Params>...</Params><ESAID>...</ESAID></TMCmd>

`DRINETTM` is the FRAME SIGNATURE, not an XML tag. The document root is
`TMCmd`, and the command number lives in `CmdID`. Worth stating plainly because
`extract_commands.py` was written assuming `<DRINETTM>` was the root element and
`<Command>` the id, and consequently found **zero** commands in a capture
containing 137 frames. Its tests passed throughout, because the fixture had
been written to the same wrong assumption.

### LED brightness is not eCmdSetDimming

The Dashboard never sends `eCmdSetDimming`. It sends **`eCmdSetConfig` (31)**
with an escaped `DRINASConfig` document:

    <DRINASConfig><General>
      <FeatureStates>7</FeatureStates>
      <SpindownDelay>14</SpindownDelay>
      <Dim>59</Dim>
    </General></DRINASConfig>

Two samples were captured, `<Dim>1</Dim>` and `<Dim>59</Dim>`, as the maintainer
changed the brightness and set it back. 59 matches what `eCmdGetDimming`
independently reports, which confirms both the field and the scale.

**`SpindownDelay` arrives free in the same block** — the standby setting that
was previously listed as blocked on a safe-to-stop check that never answers.

### Renaming sends the WHOLE network config

`eCmdSetConfig` again, carrying a complete `DRINasNetworkConfig`: Version,
NasName, NasWorkgroup, NasMinimumProtocol, the entire IPConfig block, jumbo
frames, MACAddress, PortSpeed, PortDuplex.

This is the important detail. An implementation that sent only `<NasName>`
would plausibly blank everything omitted -- IP settings, DNS, jumbo frames --
on a device with no undo. Any rename must read the current config, change the
one field, and send it all back.

### The event log is command 91, not 83

The Dashboard uses `eCmdGetUserEventLog` (91). This project had been asking
`eCmdGetEventLogs` (83), which is why it always answered empty. Not a
parameter problem at all: the wrong command. *(Corrected 2026-09-26: this
section said the old command was 90, which is `eCmdGetUserGroupList`; see
`commands.py`.)*

### Share access codes

| Code | Meaning | Status |
|---|---|---|
| `1` | Read/Write | **CONFIRMED** -- a share the maintainer states is Read/Write carried 1 |
| `0` | no access, or read-only | **UNRESOLVED** |
| `2` | unknown | never observed on the wire |

Code 0 is deliberately left open. A second share came through as 0 and the
maintainer described it as "supposed to be" read-only -- an intention, not a
reading. Zero is the natural value for "none" in most permission
enumerations, so calling it read-only risks telling somebody a share is
readable when nobody can open it. Ten seconds with the old Dashboard settles
it; until then it stays a number. See `shares.ACCESS_CONFIRMED`.

### Not captured

**Identify.** The maintainer ran every other step. Whether `eCmdIdentify` produces
any visible effect is still unknown.

### More from the same capture, found on a second pass

The first read of this capture stopped at the headline findings. Going back
through it produced three more, all CONFIRMED, and no second capture was
needed for any of them.

**DroboApps start/stop — `eCmdDroboAppsAction` (78).** Fully solved:

    <Params><Name>AppName</Name><Action>Stop</Action></Params>
    <Params><Name>AppName</Name><Action>Start</Action></Params>

(`AppName` is a placeholder for a DroboApp's name as the device lists it.) Both directions
observed. The app name is the same string
`eCmdGetDroboAppsStatus` already returns, so the list we can already read is
directly usable as the input here.

**The event log needs a path — `eCmdGetUserEventLog` (91).**

    <Params><Path>/uel.log</Path></Params>

Sent twice: once with empty `<Params>`, once with the path. That is why this
project's own attempts returned nothing even after the command number was
corrected -- an empty call appears to be accepted and answer nothing useful.

**Share permissions are written with the WHOLE share config.** `eCmdSetConfig`
(31) carrying an escaped `DRIShareConfig`:

    <DRIShareConfig>
      <ConfigVersionBasis>14</ConfigVersionBasis>
      <UserList/>
      <Shares>
        <Share>
          <ShareName>...</ShareName>
          <ShareState>0</ShareState>
          <TimeMachineEnabled>0</TimeMachineEnabled>
          <ShareMaxTMSizeGB>0.000000</ShareMaxTMSizeGB>
          <ShareUsers>
            <ShareUser>
              <ShareUsername>Everyone</ShareUsername>
              <ShareUserAccess>1</ShareUserAccess>
            </ShareUser>
          </ShareUsers>
        </Share>
        ... EVERY share, not only the one being changed ...
      </Shares>
    </DRIShareConfig>

Two things to take seriously here.

**Every share is sent, every time.** Exactly like the rename, which sends the
whole network config. A write containing only the share being edited would
plausibly delete the others. `shareedit.py` already refuses to send and already
asserts that no share is lost in a round trip -- that assertion turns out to
have been guarding precisely the right thing.

**`ConfigVersionBasis` is almost certainly a concurrency check** -- the version
of the config this edit was based on, so the device can reject a write built on
a stale read. It was 14 here. Nothing has tested what happens when it is wrong,
and until something does, any write path must echo back the value it read
rather than inventing one.

### What a future capture would still be for

Everything below is unobserved, not merely unimplemented:

| Still unknown | Why a capture would settle it |
|---|---|
| **Identify** | ~~Skipped in the 2026-08-05 session~~ **Answered on hardware 2026-08-07:** sent with empty `Params` and with `<IdentifyInterval>15`, the device returns the accept code both times and the lights do nothing visible on this unit. **Corrected 2026-09-26:** the start press had already been captured on 2026-07-25 -- the original Dashboard sends `<IdentifyInterval>900</IdentifyInterval>` (see "The command IDs are CONFIRMED") -- and this project has never sent 900. Whether 900 blinks this unit needs no capture, only a send while someone watches. Still uncaptured: the stop press |
| Creating or deleting a share | ~~Never attempted~~ **Captured 2026-08-25** — both write formats recorded; see "Create and delete a share" at the end of this file |
| Adding or removing a user | `UserList` was empty on this device, so there was nothing to observe |
| Turning Time Machine on or off | The field is visible in every share block; no change to it was captured |
| Spinning the drives down on demand | `SpindownDelay` is now known as a *setting*; a "spin down now" action, if one exists, was not seen |
| A wrong `ConfigVersionBasis` | Would show whether the device rejects stale writes, which decides how careful a write path has to be |
| What access codes `0` / `1` / `2` MEAN | See below -- a *write* capture settles this, contrary to what this document said until 2026-08-06 |

**Correction, 2026-08-06.** This section previously claimed no capture could
settle access code `0`, on the grounds that the number is already known and
only the WORD beside it is missing. That is true of a capture taken while
*reading* the config, and it is why the 2026-08-05 session did not settle it.

It is not true of a capture taken while *writing*. Cycling one share through
every named level in the old Dashboard -- applying after each -- records which
code the Dashboard writes for each word, in order. Combined with the order the
levels were picked in, that maps every code to its label from the packets
themselves.

That is strictly better than reading the word off the screen, because it is
recorded rather than transcribed from memory, and it settles `1` and `2`
alongside `0` in the same pass. The only part still owed by a human is the
ORDER the levels were applied in. `tools/capture-session.ps1` step 3 asks for
exactly that.

---

## Second capture — 2026-08-06 `CONFIRMED`

A 7-minute session, 12 MB, 564 documents on port 5001 across three
connections. Four writes in total. Identifiers below are placeholders; the
capture itself is not in the repository.

### The `<General>` block — spin-down and LED in one write

Two `eCmdSetConfig` (31) writes, in this order:

    <DRINASConfig><General>
      <FeatureStates>5</FeatureStates><SpindownDelay>0</SpindownDelay><Dim>59</Dim>
    </General></DRINASConfig>

    <DRINASConfig><General>
      <FeatureStates>7</FeatureStates><SpindownDelay>14</SpindownDelay><Dim>59</Dim>
    </General></DRINASConfig>

This is a toggle-and-restore of disk spin-down, and it confirms three things.

**`<Dim>` shares the block with spin-down.** Already known from 2026-08-05,
independently confirmed here: LED brightness is not its own command, and any
write to one of these fields must carry the other two or risk clearing them.

**`FeatureStates` is a bitmask** -- `5` is `0b101` and `7` is `0b111`, so
turning spin-down on sets bit 1 (value 2) and turning it off clears it.
MEASURED, not confirmed: only one bit was ever observed changing, so bit 1
tracking spin-down is an inference from a single pair. Bits 0 and 2 were set
throughout and nothing identifies them.

**`SpindownDelay` is `0` when disabled and `14` when enabled.** The unit is
unknown -- 14 minutes and "the 14th entry in a dropdown" are equally
consistent with one observation.

### There is no "spin down now" `CONFIRMED`

Searching the whole client-to-server stream for `Spin` returns four hits, all
of them the opening and closing tags of the two `SpindownDelay` writes above.
The operator looked for an on-demand spin-down control and reported none.

Spin-down is therefore a SETTING and nothing else. Any UI offering a "spin
down now" button would be inventing a capability the device does not expose.

### Renaming, confirmed a second time

One `DRINasNetworkConfig` write carrying the entire network configuration --
name, workgroup, full IP block, jumbo frames, MAC, port speed and duplex --
exactly as 2026-08-05 showed. It also carries a `DRINasDomainConfig` with
`<Password>`, `<ValidPassword>` and `<EncryptedPassword>` alongside it.

Those three were redacted by `extract_commands.py` on tag name alone, which is
the redaction working as designed on fields nobody had anticipated. They are
sent on a rename, by a device with no encryption, whether or not a domain is
configured.

### `UserDeleteConfirm`

The `DRIShareConfig` write carries a trailing `<UserDeleteConfirm>Y</...>`
not seen in the 2026-08-05 sample. Unexplained. It appeared on a write that
changed only share permissions and deleted no user, so it is not a
confirmation of an actual deletion and is probably a persistent preference.

### Still not captured, after two sessions

**Identify.** `eCmdIdentify` is command 26. The capture contains no command 26,
and the byte sequence `dentify` appears nowhere in either direction on either
port. Whether the operator clicked it is unresolved; if they did, the finding
is much larger than a missing parameter, because it would mean the original
Dashboard does not send `eCmdIdentify` at all.

*(Corrected 2026-09-26: true of these two sessions only. The 2026-07-25
session had already captured one press -- command 26 carrying
`<IdentifyInterval>900</IdentifyInterval>`, recorded in "The command IDs are
CONFIRMED" above -- so the original Dashboard does send `eCmdIdentify`, and
the larger finding feared here does not arise. See "What is actually unknown"
under "Identify tested on hardware".)*

**Time Machine.** No `TimeMachineEnabled>1` anywhere in the capture; the flag
stayed `0` on every share in every document. Never observed being written.

**A user record.** `UserList` was still empty in the final write, and no
user-management command appears. The shape of a user record remains unknown.

**Creating or deleting a share.** No share was added or removed. **Captured
2026-08-25** — see the dated section at the end of this file.

### Tooling: replies are being counted as requests

`extract_commands.py` reported 564 requests and 0 responses. The device's
replies use the same `TMCmd`/`CmdID` root as the commands, so the
content-based classifier -- which needs a `Result` element AND no command name
-- files them all as requests. Hence 368 `eCmdGetConfig` for 184 real reads,
and four `eCmdSetConfig` "with empty params" that are acknowledgements rather
than commands.

Harmless for reading parameters, since a reply to a write carries no `Params`
and the counts merely double. It does make "how many commands were sent"
wrong, and direction is already known per stream, so classification should
prefer direction and fall back to content.

---

## Identify is called "Blink Lights" `CONFIRMED` 2026-08-06

Two captures failed to record Identify because the operator could not find a
button called Identify. There isn't one. `eCmdIdentify` is exposed in the
original Dashboard as **Blink Lights**, on the **Tools** page. *(Added
2026-09-26: an earlier capture, 2026-07-25, had recorded the press; see
"What is actually unknown" below.)*

Read out of the installed `Drobo Dashboard.exe` (3.5.0), which carries an
embedded multi-language XML string table plus its own symbol names:

| Evidence | Value |
|---|---|
| Icon asset | `Styles\Default\Tools\blink.png`, `blink_hover.png` |
| Control name | `BlinkLights`, in `ToolsPresenter` |
| Button label | `kUIString_BlinkLights` = "Blink Lights" |
| Tooltip | `kUIString_BlinkLightsTT` = "Blink the lights on this Drobo device." |
| Toggle states | `kUIString_TurnBlinkLights_On` / `_Off` = "Turn Blink Lights ON" / "OFF" |
| Failure alert | `kAlertString_BlinkFailedTitle` = "Blink Lights Failed." |
| Handler | `ToolsPresenter::OnBlinkLights()`, `DroboDevice::Identify()` |

`ToolsPresenter` lists its controls as `BlinkLights`, `Rename`, `Format`,
`Standby`, `Restart`, `Reset`, `DeviceRepair`, `CheckForUpdate`,
`ManualUpdate` -- and the icon folder matches, so Blink Lights sits on the same
page as Rename, which we already know sends `eCmdRename`.

### It is a toggle, not a momentary action

The warning string says the lights "blink continuously for 15 minutes", and
the same button stops them. So Identify is a *mode with a duration*, not a
one-shot pulse, which changes what our UI should offer: a toggle with an
indication that it is running, not a fire-and-forget button.

### `<IdentifyInterval>` -- the parameter we could not find `CONFIRMED`

The binary contains `IdentifyInterval` inside a contiguous run of `<Params>`
tag names -- `ScaleFactor`, `ForceShutdown`, `ForceRestart`, `LunNum`,
`LunLabels`, `RotationalSpeed`, `mTemperature`. That run is the tag-name pool,
so `IdentifyInterval` is an element name, and the only command it can belong
to is Identify.

**This is very likely why our Identify appears to do nothing.** We send
`eCmdIdentify` with empty `<Params>`, per the no-guessed-parameters rule. If
the device reads a missing `IdentifyInterval` as zero, it blinks for zero time
-- accepted, no error, nothing visible. Exactly the behaviour observed.

Still a GUESS in one respect: the units. The UI says 15 minutes, so `15`
(minutes) and `900` (seconds) are both plausible, and nothing here decides
between them. A capture of the button being pressed would, and that is now a
much smaller ask than "find Identify".

**Corrected 2026-09-26: the parameter had already been found on the wire.**
The 2026-07-25 capture, the one this file's command protocol was recovered
from, holds one Blink Lights press carrying
`<IdentifyInterval>900</IdentifyInterval>`, and this file's command table
recorded it that night. The first write this project sent (860d5d8,
2026-07-27) used empty `Params`, and after that nobody went back to the table.
So the binary did not discover the name; it found it again. The value the
original Dashboard sends is 900, not 15. Next to a "15 minutes" warning
string that reads as seconds, but it stays inference until 900 has been sent
and the lights watched.

### Share permission wording, from the same string table

Three levels, which matches a 0/1/2 enumeration:

    kUIString_NoAccess         "No access"
    kUIString_ReadOnly         "(RO)"
    kUIString_ReadWriteAccess  "Allow user to write and delete files"
    kUIString_Access_Permitted "Access Permitted"

This does NOT settle which number is which. The string table holds labels, not
the enum, and no mapping between them appears in the binary's strings. Code 1
stays the only CONFIRMED one. But it does establish that "No access" is a real,
offered level -- so `0` meaning no access is a live possibility, not a
pessimistic invention.

### Method note

The string table is a resource this project had not mined. `vendor/extracted/
dashboard-strings.txt` was taken from the *installer*, whose strings are
InstallShield boilerplate; the application's own strings only exist in the
installed `Drobo Dashboard.exe`. Anything about the original UI's wording or
screen layout can be answered from there without touching a Drobo.

---

## Identify tested on hardware — empty and 15 do nothing `CONFIRMED` 2026-08-07

`eCmdIdentify` (26) was sent to a live 5N in both forms, 25 seconds apart,
control first, with the maintainer watching the unit. **Neither blinked.**

    <Params></Params>                                    -> no light
    <Params><IdentifyInterval>15</IdentifyInterval></...> -> no light

The `IdentifyInterval` theory is therefore **disproved as an explanation for
the silence**. It remains the best guess at the parameter's name; it is not
the reason nothing happens.

*(Corrected 2026-09-26. This heading said "it does nothing", which overstates
what was tested: neither form is what the original Dashboard sends. That is
900, captured 2026-07-25 and never yet sent by this project. The theory is
disproved for 15 only. The parameter's name is no longer a guess: it is on
the wire.)*

`IdentifyInterval` is also absent from all three readable config sections, so
it is not a stored setting either.

### `<Result>` 0x200000000 means "accepted", not "worked" `CONFIRMED`

Every reply carries `<Result>8589934592</Result>` -- `0x2_0000_0000`. The same
value comes back from `eCmdGetSysInfo` and `eCmdGetConfig`, which demonstrably
work, and from `eCmdIdentify`, which demonstrably does not. It is also sitting,
uninterpreted, in a sysinfo test fixture recorded weeks earlier.

So it is the success code, and the lesson is the sharper half: **accepted is
not the same as did something.** A command can return the success value and
have no effect whatsoever.

Recorded as `envelope.RESULT_ACCEPTED`. Note what is NOT known: no command has
ever returned a different value, so the failure side has never been observed.
A match means the device did not object. A mismatch would merely be worth
looking at.

This also corrected a real defect. `identify()` reported `{"sent": True}` on
the strength of a well-formed reply and never read `<Result>` at all -- so
"Identify was accepted" had been recorded twenty-odd times without anything
checking that the device agreed. It did agree. That was luck, and the same
blind spot on a command that genuinely refuses would have been reported as
success. It now returns `result_code` and `accepted`.

### What is actually unknown

*Rewritten 2026-09-26. From 2026-08-09 until then, this section said three
things. It said nobody had ever seen the original Dashboard press this button.
It said the link from Blink Lights to command 26 was inference. It said one
capture would settle it. All three were wrong: that capture already existed.*

The original Dashboard's press **was** captured, on 2026-07-25. It is the
capture this file's command protocol was recovered from (see "THE COMMAND
PROTOCOL — SOLVED" and its command table). It holds exactly one press:
command 26 with `<Params><IdentifyInterval>900</IdentifyInterval></Params>`,
answered with `<Result>` 0x2_0000_0000. So Blink Lights does send
`eCmdIdentify`, the parameter is `IdentifyInterval`, and the value is 900. The
2026-08-06 capture has no command 26 only because the operator could not find
the button.

What that leaves:

- **Whether 900 blinks this unit.** The 2026-08-07 test sent empty `Params`
  and 15, never 900. The decisive test needs no capture: send
  `POST /api/identify?interval=900` (or `py -m drobo_nasd.identify <address>
  --interval 900`) while someone watches the unit. If the lights blink, the
  unit is seconds, and `identify.DEFAULT_INTERVAL` should change from 15, this
  project's own guess, to 900. That code change waits on the test.
- **The stop press.** The capture has no second press, so `STOP_INTERVAL` = 0
  is still a guess. Recording Blink Lights being turned off would settle it.
- If 900 blinks nothing either, then the silence comes from this unit and not
  from the parameter.

---

## Spin-down: a bare `SpindownDelay=0` is coerced, not honoured `CONFIRMED` 2026-08-12

Measured on the live 5N while verifying the General-block write path. With
spin-down enabled (`FeatureStates` bit 1 set, value 7), a write of
`SpindownDelay=0` alone was **accepted with the success code and stored as
`15`** -- not 0, and not the prior 14 either. The device coerces a lone zero
delay to a default while the feature bit still says "enabled". Read-back
verification is what caught it; the accept code alone would have recorded a
working feature that silently did the wrong thing.

Nonzero delays round-trip exactly (30 -> 30, 14 -> 14), and `Dim` is untouched
by any spin-down write.

This **upgrades the 2026-08-06 capture's inference to confirmed**: turning
spin-down OFF is `FeatureStates 7 -> 5` AND `SpindownDelay -> 0` **in the same
write** -- the pair the Dashboard sends -- and neither half alone is correct. A
bare 0 is coerced (above); clearing only the bit would leave a stale delay for
re-enable to revive. Reproduced end to end on hardware:

    before        FeatureStates=7 SpindownDelay=14
    disable       FeatureStates=5 SpindownDelay=0    (verified, both fields)
    re-enable 14  FeatureStates=7 SpindownDelay=14   (verified)

`FeatureStates` bit 1 (value 2) as the enable flag is therefore CONFIRMED, not
merely inferred from one pair. Bits 0 and 2 remained set throughout and are
still unidentified.

The SDK exposes this as two operations so the trap cannot be walked into:
`droboapps.set_spindown(delay)` (nonzero only; sets the bit if it was clear)
and `droboapps.disable_spindown()` (bit + delay together). `set_spindown(0)`
is refused with an explanation before any network write.

---

## Share-permission write VERIFIED on hardware `CONFIRMED` 2026-08-12

The surgical `sharewrite.set_share_user_access` path -- audited three ways
and refutation-tested, but never before sent to a device -- was round-tripped
on a throwaway share the maintainer created in the original Dashboard for the
purpose. Full cycle, both legs accepted AND verified by read-back:

    before   basis=16   throwaway Everyone=1   (two real shares Everyone=0)
    leg 1    1 -> 0     accepted, verified     basis=17
    leg 2    0 -> 1     accepted, verified     basis=18
    after    identical to before; other shares byte-for-byte untouched

This settles, on hardware, the questions the loopback audit could not:

**The device accepts our reserialized document.** ElementTree emits
`<UserList />` (space before slash) and carries pretty-print whitespace; the
firmware's XML parser took it without complaint on two consecutive writes.
The "not byte-identical" concern is real but not a problem in practice.

**Shares writes apply synchronously.** The immediate read-back reflected the
change both times -- no delay, no stale read. Same as the General block.

**`ConfigVersionBasis` increments once per accepted write** (16 -> 17 -> 18),
consistent with it being a write counter / concurrency token. Still untested:
whether a STALE basis is rejected. Every write here echoed the just-read value.

**`<UserDeleteConfirm>` is NOT in the read reply.** DRIShareConfig's top-level
children are exactly `ConfigVersionBasis, UserList, Shares`, so our surgical
write cannot drop it -- the Dashboard adds it on its own writes and the device
does not require it (both writes above lacked it and were applied).

**A freshly created share defaults to `Everyone = 1`.** The Dashboard created
the throwaway with read/write for Everyone -- an independent, live
confirmation of code 1's meaning, from creation rather than editing.

> **Not the answer for our own create path — 2026-08-26.** A share created by
> `sharewrite.create_share` reads back with **no users at all**. This line is
> left standing because it records a real read-back of a real share; it simply
> does not describe the path this project writes. Which of the two accounts
> for the difference is **not measured** — see "RESOLVED: a share created this
> way starts with no access for anyone" in the 2026-08-25/26 hardware run at
> the end of this file.

Share creation itself is still not something the SDK does -- and a
correction, 2026-08-21: this paragraph originally claimed the create-share
format "was captured during this session". It was not. Both capture attempts
recorded nothing -- one never ran, and one was cut off by the old recorder,
which could not stop a recording early (bug fixed 2026-08-16). Create and
delete share remain capture-blocked; the re-run with the fixed recorder is
pending, and `tools/capture-session.ps1` now puts create/delete at the top
of the run. Deleting the throwaway share is likewise a Dashboard action for
now. **Update 2026-08-25: that re-run happened and recorded both writes —
see the next section.**

---

## Create and delete a share — captured `CONFIRMED` 2026-08-25

The re-run with the fixed recorder. The original Dashboard was recorded
creating a throwaway share named `ZZTESTZZ` and then deleting it — the two
share writes this project had never seen on the wire. One Dashboard, one
device, one day: as the extractor itself prints on every run, everything here
is CONFIRMED **for the case captured**, not a general specification.

The capture lives outside the repository and must never enter it: the
private create/delete-share capture from 2026-08-25 and its JSON decode are
both kept in a private folder outside the repository. Every payload below is
quoted from that decode, not reconstructed.

### Creating a share — `eCmdSetConfig` (31), basis 21

The whole `DRIShareConfig` is resent — read-modify-send, exactly like the
permission edits and the rename — with the existing `Share` nodes
byte-identical to their stored pretty-printed (tab-indented) form, and ONE
new node appended immediately before `</Shares>`, compact, on a single line.
Unescaped from the wire bytes:

    <Share><ShareName>ZZTESTZZ</ShareName><VolumeName>Volume1</VolumeName><TimeMachineEnabled>0</TimeMachineEnabled><ShareMaxTMSizeGB></ShareMaxTMSizeGB><ShareUsers/></Share>

The decoded document in full (the extractor pretty-prints; the wire layout is
as described above):

    <DRINASConfig>
      <DRIShareConfig>
        <ConfigVersionBasis>21</ConfigVersionBasis>
        <UserList />
        <Shares>
          <Share>
            <ShareName>Alpha</ShareName>
            <ShareState>0</ShareState>
            <TimeMachineEnabled>0</TimeMachineEnabled>
            <ShareMaxTMSizeGB>0.000000</ShareMaxTMSizeGB>
            <ShareUsers>
              <ShareUser>
                <ShareUsername>Everyone</ShareUsername>
                <ShareUserAccess>1</ShareUserAccess>
              </ShareUser>
            </ShareUsers>
          </Share>
          <Share>
            <ShareName>Beta</ShareName>
            <ShareState>0</ShareState>
            <TimeMachineEnabled>0</TimeMachineEnabled>
            <ShareMaxTMSizeGB>0.000000</ShareMaxTMSizeGB>
            <ShareUsers>
              <ShareUser>
                <ShareUsername>Everyone</ShareUsername>
                <ShareUserAccess>1</ShareUserAccess>
              </ShareUser>
            </ShareUsers>
          </Share>
          <Share>
            <ShareName>ZZTESTZZ</ShareName>
            <VolumeName>Volume1</VolumeName>
            <TimeMachineEnabled>0</TimeMachineEnabled>
            <ShareMaxTMSizeGB />
            <ShareUsers />
          </Share>
        </Shares>
      </DRIShareConfig>
    </DRINASConfig>

The new node differs from the stored share nodes in four ways, and each one
is load-bearing for anything that mirrors this write:

| Field | Stored share nodes | The new node |
|---|---|---|
| `VolumeName` | absent | **present** — `Volume1` |
| `ShareState` | `0` | **absent** |
| `ShareMaxTMSizeGB` | `0.000000` | **empty** — on the wire an explicit `<ShareMaxTMSizeGB></ShareMaxTMSizeGB>` open/close pair (the decode's `<ShareMaxTMSizeGB />` above is the extractor normalising) |
| `ShareUsers` | one `ShareUser` each | **self-closed empty** `<ShareUsers/>` |

That empty `<ShareUsers/>` means the create write grants nobody any access —
a new share starts with no access at all, which matches how Alpha and Beta
originally arrived at access 0. Note the tension with the 2026-08-12 line "a
freshly created share defaults to `Everyone = 1`": that was a read-back of a
throwaway created in the Dashboard that day. Today's capture shows the create
write itself carries no users — so if a fresh share ends up with access, it
got it somewhere other than the create write. Recorded, not reconciled.

**Reconciled on hardware, 2026-08-26.** A share created by this project's own
`eCmdSetConfig` read back with `<ShareUsers>` empty, immediately after
creation: a share created this way has **no access for anyone**. The capture's
reading of the create node was right. See the 2026-08-25/26 hardware run at the
end of this file.

### Deleting a share — `eCmdSetConfig` (31), basis 22

The whole `DRIShareConfig` resent again, with the `ZZTESTZZ` node simply
**absent** — there is no per-share delete command — plus exactly one new
element, appended after `</Shares>` and before `</DRIShareConfig>` (its last
child, a sibling of `<Shares>`):

    <ShareDeleteConfirm>Y</ShareDeleteConfirm>

On the wire the layout is `</Shares>`, a newline, then
`<ShareDeleteConfirm>Y</ShareDeleteConfirm></DRIShareConfig>` on one
tab-indented line. The decoded document in full:

    <DRINASConfig>
      <DRIShareConfig>
        <ConfigVersionBasis>22</ConfigVersionBasis>
        <UserList />
        <Shares>
          <Share>
            <ShareName>Alpha</ShareName>
            <ShareState>0</ShareState>
            <TimeMachineEnabled>0</TimeMachineEnabled>
            <ShareMaxTMSizeGB>0.000000</ShareMaxTMSizeGB>
            <ShareUsers>
              <ShareUser>
                <ShareUsername>Everyone</ShareUsername>
                <ShareUserAccess>1</ShareUserAccess>
              </ShareUser>
            </ShareUsers>
          </Share>
          <Share>
            <ShareName>Beta</ShareName>
            <ShareState>0</ShareState>
            <TimeMachineEnabled>0</TimeMachineEnabled>
            <ShareMaxTMSizeGB>0.000000</ShareMaxTMSizeGB>
            <ShareUsers>
              <ShareUser>
                <ShareUsername>Everyone</ShareUsername>
                <ShareUserAccess>1</ShareUserAccess>
              </ShareUser>
            </ShareUsers>
          </Share>
        </Shares>
        <ShareDeleteConfirm>Y</ShareDeleteConfirm>
      </DRIShareConfig>
    </DRINASConfig>

`<ShareDeleteConfirm>` parallels the `<UserDeleteConfirm>Y</...>` seen
2026-08-06 — and, like it, is not among `DRIShareConfig`'s read-reply
children (2026-08-12), so it is something the Dashboard adds on the write.

### `ConfigVersionBasis` 21 → 22 across consecutive writes

The create carried basis 21 and the delete, later in the same session,
carried 22 — the basis incremented by exactly one between consecutive writes,
consistent with the 2026-08-12 measurement (16 → 17 → 18, once per accepted
write). Still untested either way: whether a stale basis is rejected.

### Also in the session — recorded, NOT to be imitated

The decode also contains the following. None of it is behaviour this
project's write path copies — ours read-back-verifies instead.

- **Two `eCmdSetConfig` (31) entries with empty params** — both in the
  device→client stream, both carrying the accept code `8589934592`. These
  are the device's acknowledgements of the two writes, filed as requests by
  the extractor's known reply-as-request quirk (see "Tooling: replies are
  being counted as requests", 2026-08-06).
- **Four `eCmdForceUpdate` (14) entries** — two genuinely sent by Dashboard
  (after the create it follows an admin/apps config read; after the delete
  it comes immediately), and two device-side acks filed by the same quirk.
  Dashboard evidently nudges the device after a share write; this project
  does not send command 14 and read-back-verifies instead.
- **`eCmdGetConfig` (30) requests naming config sections never before seen
  requested.** Two request shapes, quoted from the decode:

      <Params>
        <DRINasAdminConfig>DRINasAdminConfig</DRINasAdminConfig>
        <DRINasDroboAppsConfig>DRINasDroboAppsConfig</DRINasDroboAppsConfig>
      </Params>

      <Params>
        <DRINasDomainConfig>DRINasDomainConfig</DRINasDomainConfig>
      </Params>

  Same element-name-equals-text-content convention as the known
  `<DRIShareConfig>` / `<Network>` requests. `DRINasAdminConfig` was already
  in use (`config_cmd.py`'s SECTIONS); the firsts are
  **`DRINasDroboAppsConfig`** and **`DRINasDomainConfig`** as request
  shapes, plus the first observation of **two sections requested in a single
  `<Params>`**. This partially unblocks `docs/command-surface.md`'s "whole
  config sections we do not read" backlog item: requesting these sections is
  now capture-grounded rather than guessed. Only the request shape — the
  replies were not decoded in this pass.

---

## `create_share` sent to hardware — it applied, and reported failure `CONFIRMED` 2026-08-25/26

The first time this project created a share on a real device. Supervised, one
pass, one call:

    sharewrite.create_share(host, esa, "ZZTESTZZ2", volume="Volume1")

One unit, one day, one run — as everywhere else in this file, what follows is
CONFIRMED **for the case measured**, not a general specification. Nothing here
has been repeated, and there is no second Drobo to repeat it on.

### Two measured facts that disagree

**The command was not acknowledged.** Login was accepted (`LOGIN_OK`), the
`eCmdSetConfig` frame went out, and the device then **closed the socket without
sending a result frame**. `config_cmd._read_frame` raised
`ConfigError("short header: 0 of 16 bytes")` — a clean EOF at zero bytes read,
not a timeout and not a truncated reply.

**The write applied regardless.** Read back afterwards:

| | Before | After |
|---|---|---|
| `ConfigVersionBasis` | 23 | **24** |
| Share names | `Alpha`, `Beta` | `Alpha`, `Beta`, **`ZZTESTZZ2`** |
| `Alpha`, `Beta`, `UserList` | — | **byte-identical to before** |

So a write that **succeeded** was reported to every layer above it as a
failure: `create_share` raised, the agent turns that into a 502 — its
device-side failure code — and the maintainer would have been told the share was not
created. A false negative of exactly this shape invites a retry of a write that
has already applied.

### The mirror image of this project's oldest lesson

2026-08-07 established that `<Result>` `0x2_0000_0000` means "accepted", not
"worked" — **an acknowledgement is not proof of success**. This run is that
lesson from the other side: **the absence of an acknowledgement is not proof of
failure.** Both are answered the same way and only that way — believe the
device's stored state. A write path that decides its outcome from the reply,
including from the absence of one, will eventually be wrong in one direction or
the other. Read-back is the arbiter.

### The device normalizes a created share when it stores it

The node sent is not the node kept. Read back immediately after creation, our
create node had been rewritten in four ways:

| Field | The create node carried | What the device stored |
|---|---|---|
| `ShareState` | absent | **added** — `0` |
| `ShareMaxTMSizeGB` | empty | **filled** — `0.000000` |
| `VolumeName` | `Volume1` | **dropped** — not stored at all |
| `ShareUsers` | empty | **kept empty** |

The first two bring the new node into the same shape as the shares already
stored (compare `Alpha` and `Beta` above). The third matters to every
read-modify-send that follows: whatever `VolumeName` does at create time, the
device does not retain it, so it cannot be read back and cannot be
round-tripped.

### RESOLVED: a share created this way starts with no access for anyone

The question flagged 2026-08-25 — the create write carries no users, so does a
fresh share get access from somewhere else? — has a hardware answer for this
path. **It does not.** `ZZTESTZZ2` read back with `<ShareUsers>` empty
immediately after creation: no `Everyone`, no anyone. A share created by
`sharewrite.create_share` is reachable by nobody until something grants access
to it, and `set_share_user_access` is that something.

The 2026-08-12 note "a freshly created share defaults to `Everyone = 1`" is
left standing where it is rather than deleted. It records a real read-back of a
real share, and quietly removing an inconvenient observation is how a protocol
map starts lying. What it is not is the answer for **this** path: that share
was created by the original Dashboard, `ZZTESTZZ2` by our own `eCmdSetConfig`.
Both readings are honest and they differ. Whether the Dashboard's path grants
`Everyone = 1` itself, grants it in a second write, or whether the older note
was simply mistaken, is **not measured** — nobody has read a Dashboard-created
share back at the moment of creation — and none of them may be asserted
without evidence. What is settled is the part that had to be: **a share this
project creates has no access for anyone**, so creating one must either grant
access as a deliberate second step or say plainly that access has not been
granted.

### `volume=` must be passed by the caller on this unit

This unit's stored `DRIShareConfig` contains **no `VolumeName` anywhere** — not
on `Alpha`, not on `Beta`, and (per the normalization above) not on
`ZZTESTZZ2` once stored. `sharewrite._derive_volume` therefore has nothing here
to derive from and cannot supply one; the caller must pass `volume=`
explicitly. The agent already does, through `api._new_share_volume`.

The value used, `Volume1`, is not a guess: it is what the original Dashboard's
own create node carried in the 2026-08-25 capture **of this device**. It is not
known to hold for any other unit, and a unit with more than one volume has
never been observed.

### `delete_share` has still never been sent to hardware

Only the create leg ran. `sharewrite.delete_share` has **never** been sent to a
device: its format is captured (2026-08-25) and its code is written and covered
offline, and that is the whole of it. Nothing measured here transfers to it —
in particular nothing says whether a delete is acknowledged, and a delete that
reported failure while having applied would have removed a share the maintainer
still expected to exist.

### UNRESOLVED CONTEXT — the connection model. Not a cause.

Recorded because it is the first thing anyone will reach for, and because
reaching for it without evidence is precisely the mistake this file exists to
prevent:

- The 2026-08-25 capture shows the original Dashboard drove its **whole session
  over one long-lived connection** to 5001, received the accept code
  `8589934592` for its create, and then sent `eCmdForceUpdate`.
- This project's SDK opens **one connection per command**.
- **But** the 2026-08-12 share-permission write used that same
  connection-per-command pattern, **did** receive a result frame, and verified
  clean. Connection-per-command is therefore not broken in general.

Something about creating a share specifically is different. What that something
is **has not been measured**, and no cause is stated here — not the connection
model, not a share/Samba config reload, not anything else. Do not promote this
section to an explanation, here or anywhere else in the project. The way to
settle it is a capture of **our own** create — the same write, recorded on the
wire — not an argument from the Dashboard's capture.

### A leftover share is on the maintainer's device, deliberately

`ZZTESTZZ2` still exists on the unit, with **no access for anyone** (empty
`ShareUsers`, above), and it was **left there on purpose**. It is the maintainer's
device and the maintainer's decision. Do not delete it, do not tidy up after this
run, and do not send `delete_share` at it — that would be both an unrequested
write and the first-ever hardware use of an unverified path, aimed at the
maintainer's own data.
