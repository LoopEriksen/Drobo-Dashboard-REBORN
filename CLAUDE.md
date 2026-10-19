# Drobo Dashboard REBORN — working notes

Replacing the discontinued Drobo software. Drobo/StorCentric liquidated in 2023,
`drobo.com` no longer resolves, and both iPhone apps are delisted. The hardware
still works, so this project rebuilds the client software around it.

**This is a standalone project.** It shares no code, data, or dependencies with
anything else, and other projects should never be brought into a Drobo session
unless the user explicitly links them here.

**Sessions for this project launch from this repository's own folder**
(decided 2026-08-12), so the session's home, memory, scratchpad, and
auto-filled command targets all belong to this project and nothing else. Do
not work on this repo from a session rooted elsewhere. Temporary artifacts
that carry device data (capture decodes, agent logs, screenshots) go to a
private folder outside the repository, never into another project's space --
and never into this repo (see the captures rule in `.gitignore`).

**This repository is the public one** (decided 2026-10-08; ROADMAP Phase 6).
Every tracked byte is published, with a fresh history; the earlier history is
kept privately and never pushed here. No other repository is touched from a
session here: the separate public export it replaced is retired, and so is
the export procedure that fed it.

## Target hardware

A **Drobo 5N** — network model, so Dashboard talks to it over Ethernet and the
protocol can be captured with ordinary Wireshark. Marvell Armada XP (ARMv7),
2 GB RAM, SMBv2. Last firmware 4.3.1 (pulled by Drobo); 4.2.2 is the last
known-good. Kernel is **Linux 3.2.96** per the 4.2.1 firmware's uImage (older
community figures cite 3.2.58 from an earlier release).

## How we develop

**[`ROADMAP.md`](ROADMAP.md) is the plan of record** — six phases, in order, with
a rule that a phase doesn't start until the one before it works. Re-read it when
work drifts. Features discovered by reading Drobo's own firmware are listed there
awaiting approval; **do not build one without asking first.**

Two companions, both kept against the code rather than against intentions:
[`docs/capabilities.md`](docs/capabilities.md) is the inventory of what the
software does and how far each part has got; [`docs/development-history.md`](docs/development-history.md)
is the path taken, in order, including the turns that were wrong. When a
feature lands, moves status, or is cut, update both — they are truth records,
and a stale row in either is a false claim.

Do it in the same commit as the change, and move the roadmap's `Last updated`
date with it: both documents had drifted by a fortnight before anyone noticed,
which is exactly what this rule exists to prevent. The roadmap's front matter
carries fixed sections — what this is, where we are, working on now, features,
future features — whose headings and status words are read by tooling outside
this repository, so keep the headings and the words as they are. Everything below
the divider is this project's own record and is not bound by that shape.

## Current state

| Piece | Status |
|---|---|
| **`Drobo5NDriver`** | **WORKING against the real device.** `sdk/drobo_nasd/esatm.py` reads the `DRINASD\0` status greeting nasd pushes on connect (no login) and returns a live Snapshot; `sysinfo.py` tops that up with real chassis temperature/uptime over the command channel. The agent serves real data with `"driver": "drobo5n"`. |
| `agent/` + web dashboard | **Working** on real *or* simulated data. Test suites live in `agent/tests/`, `sdk/tests/` and `tools/test_*.py` (five), all passing — see `agent/README.md` for the suite list and `test_agent.py`'s current check count. Chassis temperature is wired end-to-end with its own alert thresholds, and performance is wired into `Drobo5NDriver` and ships (an earlier note here said "not yet wired" — false since the 2026-07-26 unparking; corrected 2026-08-21). Cache battery/fan/PSU still read UNKNOWN from the real device (see `ROADMAP.md`'s Parked section); the event log is fetched for real since 2026-08-21 (`eventlog.py`), its per-line format still undecoded. |
| **`windows/DroboDashboardReborn/`** | **Built and launches** — native WPF app (.NET 10): five tabs — Shares (list, open/connect-as/disconnect, mount, in-app file browser, create/delete), Status (device/capacity/bays/volumes, health card, rebuild/double-degraded notice), Alerts (worst-first with coloured chips, indicator dot, calm empty state), Settings (The Drobo: read-only network config and versions; Preferences), Tools (versions, open web dashboard, copy diagnostics, guide link, backup, Identify, DroboApps list with start/stop (not admin-gated), event log viewer, and the Advanced Configuration card behind an in-app admin/admin sign-in — a safety catch, not device security: brightness, spin-down, share access). Publishes self-contained (~155 MB exe). `dotnet run -c Release --project windows/DroboDashboardReborn`. WPF first (verifiable build); WinUI 3 later. |
| `sdk/drobo_nasd/discovery.py` | **Working.** Finds the Drobo without a static IP — mDNS `_nasd._tcp`, `.local` hostname, last-known IP, optional bounded subnet sweep — then confirms the candidate's `mESAID` against the expected serial (`IdentityMismatch` if it doesn't match) before the driver trusts it. |
| `sdk/drobo_nasd/` (commands/envelope/config_cmd/sysinfo) | **The command (send) path is solved and used routinely** — TCP 5001, `DRINETTM` framing, a 220-byte login carrying the device serial twice. `config_cmd.py` fetches real config; `sysinfo.py` fetches real temperature/uptime; `tools/nasd_sweep.py` and `tools/sweep_remainder.py` have swept 46 commands under a plain login across two sweeps, 2026-07-26/27 (`docs/command-surface.md`). The older speculative `client.py`/`framing.py` (`NasdHeader` candidates) predates this and was never reconciled with it — still cannot talk to real hardware, kept only for its own test coverage. |
| **`START DROBO DASHBOARD.bat`** | Repo-root double-click launcher — starts the agent and launches the newest Windows app that `tools/find-app-exe.ps1` finds (installed copy, packaged build in `windows/dist/`, or a local build; newest file wins). Deliberately does not open the browser: the app's Tools tab has "Open web dashboard". |
| **Installer** (`windows/installer/`, `windows/build-installer.ps1`) | **Tester-ready since 2026-09-02; per-machine since 2026-09-08.** A WiX MSI installing to `C:\Program Files\Drobo Dashboard REBORN` for all users (so it prompts for elevation, and `test-installer.ps1` now requires an elevated shell) that **bundles the Python runtime** (embeddable 3.14, version and SHA-256 pinned in `python-embed.lock`), so a tester installs nothing else. The agent's `config.json` (with its token) lives in `%LOCALAPPDATA%\DroboDashboardReborn` and is kept on uninstall, the way most apps keep settings; the program folder is removed completely, generated files included. Start Menu **Uninstall** entry; `TESTING.md` ships inside the install folder. `windows/test-installer.ps1` proves install → launch via the real launcher against the simulated driver → uninstall → upgrade; the count depends on which legs run — the recorded elevated runs were 53 checks on 2026-09-18 (no upgrade leg) and 64 on 2026-09-21 (with it). Smart App Control, where it is enforced, blocks the unsigned app and the `wix` shim, so the build runs WiX through `dotnet wix.dll` instead. **Unsigned on purpose — code signing was decided against 2026-09-05 ("not for profit"); do not raise it as a to-do.** SmartScreen says Unknown publisher; a SAC machine turns SAC off (reversible since April 2026) or uses the browser dashboard. |
| Protocol | **Both the read path (port 5000) and the command path (port 5001) are CONFIRMED on live hardware.** Chassis temperature ships; cache battery/fan/PSU remain unanswered (empty or timeout) over the command channel. The event log's real mechanism is now captured — `eCmdGetUserEventLog` (91) with `<Path>/uel.log</Path>`, 2026-08-05, and the fetch is **built and wired** (`sdk/drobo_nasd/eventlog.py` → `drivers.py` → agent + WPF viewer); only the reply's per-line ts/severity format is still undecoded, so entries carry explicit NOT_DECODED markers instead of invented ones. The earlier "answers empty" came from sending command 83 (`eCmdGetEventLogs`) with empty params (this said 90, which is `eCmdGetUserGroupList`, until 2026-09-26). Create/delete-share write formats **captured 2026-08-25** (whole-config resend; compact new-share node / `<ShareDeleteConfirm>Y</...>`). Create was then **applied on the real device once, 2026-08-25/26** — and the device closed the connection instead of answering, so a write that succeeded was reported as a failure; that acknowledgement bug was **fixed in `b1c2064`** (`config_cmd.decided_by_readback` — the read-back now decides an unacknowledged write), but create has **not been re-run on hardware since the fix**, so it stays **implemented, not shippable-verified**. Delete has **never been sent to hardware**. Both are built, wired and ungated at HEAD and listed under Added for 1.1.0 in `CHANGELOG.md` — **built, unverified on hardware**. The device also normalizes a created share (adds `ShareState`, fills `ShareMaxTMSizeGB`, drops `VolumeName`) and gives it **no access for anyone**. See `docs/protocol-map.md` and `docs/command-surface.md`. |
| iOS app (SwiftUI) | Not started — **needs a Mac to build at all**. Agent-side upload API is done. |
| Phases | **Phase 1 is not complete** (ROADMAP's Phase 1 row): the original definition of done is met, but the finish line moved on 2026-07-27 to "match what Drobo Dashboard actually did", and against that bar renaming the Drobo, share create's re-run and delete's first send, user create/delete (capture-blocked), `eCmdStandby` (scope undecided) and the DroboSync trigger (needs a second unit) remain; decoding `mStatus`/`DNASStatus` also needs a degraded array, and the parser fails safe meanwhile. **Phase 2 mostly built** — dark mode, desktop/ntfy/email notifications, the installer and dormant updater, DroboApps start/stop (built, never sent to hardware), share access writes (hardware-verified), share create (applied once, not re-run since the ack fix) and delete (never sent); not built: thumbnails, user create/delete, rename. Phases 3–5 not started (needs a Mac; design questions open; Tailscale unbegun). Cache battery/fan/PSU parked since 2026-07-25 — a 5N reports none. The full inventory with status is `docs/capabilities.md`. |

## Ground rules

- **Never propose flashing firmware on the live 5N.** Custom firmware is a
  separate fork (`docs/archive/BLUEPRINT.md` §9) and needs a sacrificial second unit first,
  because BeyondRAID's on-disk format is undocumented and a modern kernel can't
  read an existing disk pack.
- **Writes reach the device only through the gates.** The project was
  read-only until 2026-07-27. Today `commands.SAFE_WRITES` (Identify) and
  `VETTED_WRITES` (`eCmdSetConfig` only via `config_cmd.set_config`'s
  read-modify-write; `eCmdDroboAppsAction` only via `droboapps_action`) are
  the only ways bytes that change anything go out, and 53 of the 103 commands
  are refused outright. Nothing joins either set without the maintainer saying so.
  This device is irreplaceable and there is no vendor recovery tool.
- **Never expose the Drobo or the agent to the internet.** No port forwarding.
  Remote access is Tailscale on a separate always-on box.
- **Packet captures may contain the Drobo password in plain text.** They are
  never committed; see `docs/captures/README.md`.
- **There is a leftover share `ZZTESTZZ2` on the maintainer's unit** — created by the
  2026-08-25/26 hardware run, empty, no access for anyone, and **left there
  deliberately** pending the maintainer's decision. Do not delete it, do not tidy it
  up, and do not point `delete_share` at it: that path has never been sent to
  hardware.
- **Read `docs/protocol-map.md` before assuming something is unknown.** It's
  filled in from firmware analysis plus live measurement, and both the status
  path (port 5000) and the command path (port 5001, header and login included)
  are confirmed against real hardware — check there before assuming a "capture
  needed" blocker still applies.
- **Nobody but the maintainer edits this repository** (decided 2026-09-01). It
  is public for **feature testers**, not a collaboration repo: people run it
  on their own hardware and report back as issues. Never propose pull-request
  or contribution workflow. Public-facing text says *testers*, never
  collaborators.
- **Every push to this public repository ships the changelog** (`CHANGELOG.md`;
  standing rule, 2026-09-01, restated 2026-10-08 when this repository became
  the public one). Update it in the same commit as the change, with
  `docs/capabilities.md`, `docs/development-history.md` and
  `docs/testers-tasks.md` where the change touches them.
- **Everything tracked is public, so nothing personal goes in** (decided
  2026-10-08). No real serial, MAC address, IP address, host, network, share
  or account name from the maintainer's unit; no drive make-up of that unit;
  no person's name or email beyond the published credits; no user-profile or
  sync-folder path; nothing about the maintainer's PC, network or
  circumstances. Fixtures recorded from the real device carry synthetic values
  in the same format. Author commits with the account's GitHub no-reply
  address. The privacy guard in `scripts/` checks every commit, but it is a
  backstop, not permission.

## Layout

```
ROADMAP.md                the plan of record: phases, dated decisions, status
CHANGELOG.md              what changed between releases; every push ships it
docs/README.md            index of every document, grouped by who it is for
docs/capabilities.md      what the software does and how far each part has got
docs/development-history.md   how it was built, in order, wrong turns included
docs/archive/             finished or superseded notes, kept for the record -- see docs/archive/README.md
docs/archive/BLUEPRINT.md the original blueprint (converted from the .docx); history -- ROADMAP.md (the order of the work), capabilities.md and development-history.md win where they disagree
docs/protocol-map.md      the protocol reference — both channels, the login, every write format, dated
docs/command-surface.md   every command swept on the command channel, and what answered
docs/running-the-agent.md running the agent at boot; how apps connect
docs/testers-tasks.md     what needs hardware this project lacks, with read-only steps
docs/accessing-your-files.md   the Shares guide the Windows app opens from its Tools tab; never move or rename it
agent/                    the always-on service (Python, stdlib only)
agent/tests/              the ten agent test suites (sdk/tests/ holds the SDK's sixteen)
sdk/drobo_nasd/           the protocol SDK (split out of the agent 2026-07-27)
sdk/drobo_nasd/discovery.py   finds the Drobo by identity, no static IP
sdk/tests/                the SDK's sixteen suites (fixtures/ holds the sanitised status sample)
windows/DroboDashboardReborn/   native WPF app (.NET 10)
windows/DroboDashboardReborn.Tests/   notification-rule and saved-login tests
windows/installer/        WiX MSI source, launchers, TESTING.md, python-embed.lock
windows/build-installer.ps1, windows/test-installer.ps1   build the MSI; prove its lifecycle
tools/drobo_probe.py      read-only network discovery
tools/README.md           every tool by job, and which ones contact a real Drobo
scripts/                  the PII guard (scanner + pre-commit hook); never move or rename anything in it
.github/workflows/        CI: guard.yml (secrets + PII), lint.yml (every .py compiles), tests.yml (all thirty-one Python suites, and the Windows app + its tests)
docs/captures/            local-only packet captures; README.md is the only tracked file
ios/                      iPhone app plan only (needs a Mac)
vendor/                   Drobo's own software, local only; only README.md is tracked
"START DROBO DASHBOARD.bat"    double-click launcher (agent + app; it does not open the browser)
"START WITH SIMULATED DROBO (testing).bat"   double-click: the app against a simulated Drobo, no Drobo needed (the app still runs a real mDNS search when it starts)
"CAPTURE DROBO COMMANDS (run me).bat"   double-click (asks for admin): records the original Dashboard, only when a capture is asked for
```

`agent/drobo_agent/drivers.py` owns the polling path to the Drobo, and
`api.py`'s on-demand routes (config reads and writes, DroboApps, DroboSync,
the event log) call `sdk/drobo_nasd` directly, one request at a time. Nothing
else in the agent speaks the protocol, and the protocol itself lives only in
the SDK. Everything above deals in the plain shapes from `models.py`. Keep it
that way — it's what lets the mock driver stand in for the real one.

## Commands

```bash
py agent/run_agent.py            # start the agent
py agent/run_agent.py --once     # one reading as JSON, then exit
py agent/run_agent.py --open     # start, then open the dashboard in your browser
py agent/run_agent.py --config X # use a config file other than agent/config.json
py agent/tests/test_agent.py     # end-to-end checks, no hardware needed (see agent/README.md for the current count)
py tools/drobo_probe.py mdns     # find the Drobo on the LAN
```

Python 3.14, standard library only. Run `test_agent.py` after any change under
`agent/` — especially before switching the driver to the real 5N.

## Decisions made 2026-07-23

- Scope is the **apps only**, running against stock unmodified firmware. The
  *apps* are what gets kept updatable.
- Firmware *updating* cut from v1 entirely. Custom firmware authoring deferred
  to a fork.
- Shared layer is an **HTTP/JSON agent, not an FFI SDK** — no binding
  toolchains, and it also solves iOS push and remote access.
- Agent is **Python**, not C#. The HTTP boundary means the Windows app can
  still be C#/WinUI 3.
- iOS aims at health, alerts and photo backup — *not* file browsing, which the
  built-in Files app already does over SMB.
