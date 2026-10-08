# drobo-nasd

**Talk to a Drobo NAS from Python.** The protocol, recovered from the official
firmware and confirmed against a live Drobo 5N.

Drobo's parent went into liquidation in 2023. `drobo.com` no longer resolves,
the apps were pulled, and there was never a published protocol. The hardware
still works. This is what it speaks.

Standard library only — nothing to install, nothing to rot.

```python
from drobo_nasd import esatm

snap = esatm.read_snapshot("10.0.0.5")
print(snap.device.name, snap.device.firmware)
for d in snap.drives:
    print(f"bay {d.bay}: {d.state} {d.model} {d.capacity_bytes/1e12:.1f} TB")
```

## In this folder

| | |
|---|---|
| `drobo_nasd/` | The library, one module per job: `esatm.py` (the status the Drobo sends the moment you connect), `discovery.py` (finding it on the network and confirming it by serial), `config_cmd.py` (reading and writing its settings), `sysinfo.py` (temperature, uptime, throughput), `shares.py` (the share list, and why Windows may refuse a share), `shareedit.py` (builds a changed share configuration and cannot send it) and `sharewrite.py` (sends share changes: permissions, create, delete), `droboapps.py` (DroboApps, LED brightness, spin-down, shop-demo mode), `identify.py` (flash the lights), `eventlog.py` (the device's event log), `drobosync.py` (Drobo-to-Drobo replication, read only), `netcheck.py` (is this PC on the Drobo's network?), `firmware.py` (what a firmware version means), `commands.py` (all 103 commands, and which are refused or allowed to write), `envelope.py` (the XML a command travels in), `models.py` (the data shapes it returns), `__init__.py` (an overview, the safety rules and the version number). `client.py` and `framing.py` are an early guess at the protocol, kept only for their own tests |
| `tests/` | Sixteen test suites that need no hardware; the sample status reply they share (`fixtures/`, sanitised from a real one); and `conftest.py`, which only lets `pytest` find the library. Run any suite with `py <file>`, from inside `tests/` or from the repository's top folder. `test_discovery.py` also tries an optional read-only check against a real Drobo at a placeholder address (or `DROBO_LIVE_HOST`), and skips it when nothing answers there |
| `pyproject.toml` | Packaging details, for installing the library with pip |

## What works

| | |
|---|---|
| **Status** | Identity, firmware, per-bay drive health, capacity, volumes, pack health |
| **Discovery** | Find a Drobo by mDNS and confirm it by hardware serial, not IP |
| **Configuration** | Network settings, shares and their permissions, admin account |
| **Sensors** | Chassis temperature, uptime, throughput and IOPS |
| **Software** | DroboApps installed on the device |
| **Identify** | Flash the front lights |
| **Event log** | The device's own event history (`eCmdGetUserEventLog`); per-line timestamp/severity format still undecoded, marked as such |
| **DroboSync** | Replication settings and logs; reports "not configured" honestly on a single unit |
| **Writes, vetted** | LED brightness, spin-down delay, per-user share access — each through a read-modify-write of the device's live config that refuses unknown fields; verified on hardware |
| **Writes, built but unproven** | Create a share (applied to a device once), delete a share (never sent), and DroboApp start/stop (a direct command validated against the device's own app list; never sent to a device by this SDK). Every write ends one of four ways, which the agent reports as `verified`, `verified_unacknowledged` (applied, but the Drobo hung up instead of answering, so reading the setting back settled it), `failed` or `unknown` |

Status of every capability, checked against the code, is kept in
[`../docs/capabilities.md`](../docs/capabilities.md); the safety boundary
(53 of 103 commands refused in code, one safe write, two vetted) is described
there and enforced in [`drobo_nasd/commands.py`](drobo_nasd/commands.py).

Two channels, both solved. **TCP 5000** pushes a complete status document the
instant you connect, no login. **TCP 5001** takes commands after a 220-byte
login carrying the device serial twice.

## What it refuses to do

This talks to hardware holding somebody's only copy of their data, on a device
that will never get another firmware update and has no vendor recovery tool.

`commands.DO_NOT_SEND` refuses **53 of the 103 known commands** — six
arbitrary-execution escape hatches and forty-seven destructive ones: format,
firmware flash, reset, repair, every `eCmdSet*`. Fifty-one of those carry a
confirmed id in the table; two are refused by name alone. Enforced in code.

`commands.SAFE_WRITES` is the narrow exception, and holds exactly one entry:
`eCmdIdentify`. A second tier, `commands.VETTED_WRITES` (`eCmdSetConfig`,
`eCmdDroboAppsAction`), stays refused for the ordinary send helpers but has
exactly one sanctioned sender each — `config_cmd.set_config`'s
read-modify-write and `droboapps.droboapps_action` — that makes it safe by
construction rather than by command id. A command may only join it if it cannot touch the disk
pack, cannot affect availability, is reversible, and costs nothing worse than
mild confusion when it goes wrong.

**Two names in the table lie**, and both are refused:
`eCmdForceSingleInitiator` reads like a query but writes, and
`eCmdGetNextUniqueLUNID` starts with `Get` but *allocates*.

Parameters are sent **empty unless their exact shape was captured** from the
original Dashboard on the wire (the config reads and writes, the event log's
`<Path>`, Identify's interval). The firmware gives ids and names but
never documents `<Params>`, and a guessed parameter is an unreviewed
instruction to somebody's storage.

## What is not known

Some things are measured, some are guessed, and the docstrings say which.

- **Share access codes.** Stored as bare integers. What `1` versus `2` means is
  computed at runtime and is not in the firmware — only Samba's own libraries
  mention read/write lists. So `shareedit` refuses to invent a code: it writes
  only a confirmed one (code 1 = read/write, measured on hardware 2026-08-12)
  or "no access", and `shares` shows code 0 hedged and any unobserved code
  by its number rather than claiming a share is "read-only".
- **Pack status codes.** `mStatus`, `DNASStatus` and friends are reported as
  observed values with no invented meaning.
- **The command port tires.** After roughly ninety connections in an hour,
  `eCmdGetSysInfo` stopped answering entirely while the array stayed healthy,
  and recovered on its own in about half an hour. Ask rarely.

## Requirements

Python 3.10 or newer. Nothing else.

## Unaffiliated

Not connected with Drobo, StorCentric, or their successors. No Drobo software
is redistributed here. Reimplementing a protocol to keep hardware you own
working is ordinary interoperability.
