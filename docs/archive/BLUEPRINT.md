# Drobo Modernization Blueprint

*(Essential Components + Selected Optional Features)*

> **Phase ordering lives in [`ROADMAP.md`](../../ROADMAP.md), which is authoritative.**
> This document covers *what* gets built; the roadmap covers *when*, and is the
> one to re-read when work drifts. Where the two disagree about sequencing, the
> roadmap wins.
>
> **Archived.** This Markdown copy replaced the original
> `Drobo Modernization Blueprint.docx`, which was removed from version control
> on 2026-08-10 (Office files are gitignored since 2026-09-01): a privacy audit
> found the author's real name in its Word metadata. It is kept for the record and is no longer edited; the plan now
> changes in [`ROADMAP.md`](../../ROADMAP.md).
>
> Decisions made after the original draft are marked inline as
> **Decision (date)**. Items the review flagged as unachievable are marked **Correction**
> with the reasoning in [`docs/archive/reality-check.md`](reality-check.md).
>
> **This is the intent as it stood at the start.** What was actually built,
> and how far each part has got, is kept against the code in
> [`docs/capabilities.md`](../capabilities.md); the order it happened in,
> including where this plan bent, is [`docs/development-history.md`](../development-history.md).
> Where this document and those two disagree, they are current and this is
> history.

---

## 0. Target hardware and scope

**Decision (2026-07-23):** the target is a **Drobo 5N**.

| | |
|---|---|
| Model | Drobo 5N (5-bay, network / Ethernet) |
| SoC | Marvell Armada XP MV78460 — ARMv7, quad-core 1.2 GHz |
| RAM | 2 GB DDR3 |
| OS | Linux kernel 3.2.96-3 (the uImage name inside firmware 4.2.1; corrected from an earlier draft's 3.2.58, which came from an older community figure — see `docs/protocol-map.md`) |
| SMB dialect | SMBv2 (the newer 5N2 and B810n do SMBv3; the older FS is SMBv1) |
| Last firmware | 4.3.1 (May 2022) — **pulled by Drobo after release**; 4.2.2 is the community's last-known-good |
| DroboApps | Yes — SDK 2.x, community archive at `github.com/dhomas1/DroboApps` |

Being a network model is good news: Dashboard talks to it over Ethernet, so the
protocol can be captured with ordinary Wireshark. No USB/SCSI reverse
engineering needed.

**Scope of this project:** rebuild the *client software* for the hardware as it
exists today, and make **the apps** self-updating so they keep working as
Windows and iOS move on. The Drobo's own firmware is left exactly as it is.

**Decision (2026-07-23):** custom firmware is out of scope for v1 and moves to a
separate fork — see [§9 Future Plans](#9-future-plans--custom-firmware-fork).
Everything in v1 must work on stock, unmodified 4.2.2/4.3.x firmware.

---

## 1. Unified Drobo SDK

**Overview.** A cross-platform software development kit that handles all
communication between the Drobo NAS and the new Windows/iOS apps.

### Responsibilities

- Network discovery
- Authentication
- XML telemetry parsing
- RAID/BeyondRAID status parsing
- Drive health & temperature parsing
- File browsing
- Upload/download operations
- DroboApps endpoint management
- SSH-based system diagnostics
- Clean API surface for Windows and iOS apps

### Implementation notes

Original plan: Go or Rust, with bindings for Swift (iOS) and C# (.NET Windows),
serving as the single source of truth for Drobo logic.

> **Decision (2026-07-23): option (b), the Drobo Agent — and it is built.**
> Running code is in [`agent/`](../../agent/); see [`agent/README.md`](../../agent/README.md)
> and [`docs/running-the-agent.md`](../running-the-agent.md).
>
> One revision to the recommendation below: **the agent is written in Python,
> not C#.** Python 3.14 was already in the development toolchain where a .NET
> SDK was not, and the agent needs nothing beyond its standard library. The HTTP/JSON boundary
> means the agent's language never has to match the apps' — the Windows app is
> still C# (it shipped as WPF on .NET 8, not WinUI 3 — see §2). If the agent ever moves to a Raspberry Pi, Python moves
> with it unchanged.
>
> The two options as originally weighed:
>
> **(a) As originally written** — Rust or Go core, FFI bindings to Swift and C#.
> Standard commercial architecture. Cost: you maintain a core, two generated
> binding layers, two toolchains, an iOS XCFramework build and a NuGet package
> before the first screen renders. Go's mobile bindings are effectively
> unmaintained; Rust's (UniFFI) are the better of the two.
>
> **(b) Recommended — a "Drobo Agent" that speaks HTTP/JSON.** One always-on
> service (C# / .NET 8, running on the Windows PC or a Raspberry Pi) is the only
> component that knows the Drobo protocol. Both apps talk plain JSON to it over
> REST + WebSocket. No FFI, no binding toolchain — and it also solves iOS push
> notifications (§3) and remote access (§6), because something has to be awake
> and watching anyway.
>
> Trade-off with (b): the iOS app's *health* features need the agent running.
> Mitigated by giving iOS two modes — **direct** (SMB file access, works with
> nothing else running) and **agent-assisted** (health, alerts, push, history).
>
> Full reasoning: [`docs/archive/reality-check.md` §4](reality-check.md).

Whichever wins, the rule holds: **Drobo protocol knowledge lives in exactly one
place.**

---

## 2. Windows App (planned as WinUI 3 + .NET 8; shipped as WPF on .NET 8)

> **Decision (2026-07-24):** WPF first, because it is the toolchain that
> reliably builds; the web dashboard served as its layout prototype. WinUI 3
> is optional later polish, not a gap. Everything below about *what* the app
> does stands; only the UI framework changed.

A modern replacement for Drobo Dashboard with native Windows 11 design.

### Core features

- Drive health display
- Temperature monitoring
- Capacity visualization
- RAID/BeyondRAID status
- Alerts and warnings
- File browser
- Upload/download support
- DroboApps management
- ~~Firmware update support~~ → **read and display only**
- System diagnostics

> **Correction — firmware updating is cut from v1.**
> No new Drobo firmware will ever be released; the last two builds (4.3.0 and
> 4.3.1, 2022) were pulled by Drobo's own engineers over user-reported problems,
> and 4.2.2 is the last known-good. A home-built flasher writing an unsigned
> image from a third-party mirror to irreplaceable hardware, with no vendor
> recovery tool, is the only feature in this blueprint that can destroy the
> device — in exchange for installing firmware you either already have or
> shouldn't want.
>
> **v1 behaviour:** display the running firmware version, and warn if it's
> 4.3.x. Flashing stays a manual job with the old Dashboard.
> Writing firmware moves to the fork in §9.

### Included optional features

- **Dark Mode** — automatic theme switching based on Windows settings.
- **Push Notifications** — native Windows toast alerts for drive failures,
  overheating, low capacity, and system warnings.
- **Background Transfer Manager** — parallel transfers, pause/resume, retry
  logic, persistent queue, and transfer history.
- **Local Thumbnail Generation** — fast, on-device thumbnail generation for
  photos and videos stored on the Drobo. *(Renamed from "Local AI Thumbnail
  Generation" — thumbnailing is decoding and downscaling, no model involved.
  Genuine AI features like photo indexing stay in §7.)*
- **Modern Installer / Auto-Updater** — clean MSI installer, automatic updates,
  and removal of legacy Drobo services.

> **Decision (2026-07-23):** the auto-updater is **core scope, not optional.**
> "Making it updatable" is the point of the project — an app that can't ship
> itself a fix is the exact failure mode that killed the original.

---

## 3. iOS App (SwiftUI)

A modern replacement for DroboPix and DroboAccess.

> **Note:** both originals are gone from the App Store — verified by direct
> query to Apple's API: DroboAccess (id 1092803136) and DroboPix (id 1128168682)
> both return zero results and their store pages 404. This is a from-scratch
> build, not a port. A paid Apple Developer account ($99/yr) is required for
> TestFlight and for the app to run longer than 7 days on your own phone.

### Core features

- Drive health
- Capacity display
- Alerts
- File browser
- Upload/download
- Optional photo backup
- Optional remote access

> **Scope note — aim at what iOS can't already do.** Since iOS 13 the built-in
> Files app has an SMB client, and the 5N speaks SMBv2, so it mounts natively
> with zero software. Basic file browsing is therefore the *least* valuable
> thing this app can offer. The differentiators are **health, alerts, and
> automatic photo backup**. For the file plumbing, `AMSMB2` (maintained Swift
> wrapper around libsmb2) beats writing an SMB client.

### Included optional features

- **Dark Mode** — automatic theme switching based on iOS settings.
- **Push Notifications** — real-time alerts.

  > **Correction — needs a server; the Drobo can't be one.** APNs only accepts
  > pushes from a server holding your developer credentials, and iOS won't let
  > an app sit in the background polling a NAS. For v1, have the agent (§1b)
  > push through **ntfy** (free, self-hostable, has an iOS app) or **Pushover**
  > (one-time fee); swap in your own APNs pipeline later.

- **Share Sheet Integration** — send files to Drobo from any app using the iOS
  share menu.
- **Background Transfer Manager** — background uploads/downloads, retry logic,
  parallel transfers, and iOS background task support.
- **Local Thumbnail Generation** — fast, on-device thumbnail generation for
  photos and videos.
- **Modern Deployment Pipeline** — TestFlight builds, CI/CD updates, and modern
  signing/provisioning.

> **Worth copying from DroboPix:** it only auto-uploaded when the phone saw both
> the home Wi-Fi SSID *and* was inside a geofence, and only over Wi-Fi — good
> design, and none of it depended on Drobo's cloud. *(Unconfirmed: Drobo's own
> pages list DroboPix support for 5N2/B810n but DroboAccess for 5N/5N2/B810n, so
> whether DroboPix ever officially supported the 5N is unclear. Irrelevant to a
> from-scratch build, but worth knowing if a capture behaves oddly.)*

---

## 4. Drobo Protocol Map

A reverse-engineered reference describing how the Drobo communicates.

### Components

- XML telemetry format
- SSH commands for system stats
- HTTP endpoints for DroboApps
- Authentication flow
- File transfer endpoints
- Error codes
- BeyondRAID capacity math

**Live document:** [`docs/protocol-map.md`](../protocol-map.md) — currently an
empty template *(as of 2026-07-23; it is now an 1800-line reference — both
channels confirmed on hardware, the login, all 103 command ids, every write
format, dated)*. Filling it in was the entire content of Phase 1.

> **This is the project's critical path.** Drobo published no protocol
> documentation, the company is liquidated, and `drobo.com` no longer resolves.
> The last Drobo Dashboard build for Windows (3.5.0, Dec 2019) still runs on
> Windows 11 — and it is now the only surviving description of this protocol
> anywhere. Capture it before that stops being true, and do not uninstall it.

---

## 5. Reverse Engineering Workflow

### Tools

- Wireshark
- Charles Proxy / Fiddler
- SSH logging
- Dashboard binary inspection
- DroboApps endpoint exploration
- [`tools/drobo_probe.py`](../../tools/drobo_probe.py) — discovery, port sweep and
  fingerprinting; read-only, never sends the Drobo a command

### Goals

- Capture all communication between Drobo Dashboard and the NAS
- Document every endpoint
- Reproduce all functionality in the SDK/agent

**Procedure:**
[`docs/archive/phase-1-capture-playbook.md`](phase-1-capture-playbook.md) — one
capture per Dashboard action, with a checklist. Because the 5N is a network
model, this is straight Ethernet capture; the USB/SCSI path in the playbook's
Step 5 does not apply and can be ignored.

---

## 6. Basic Security Modernization

> **Correction — retitle this "Security posture modernization."** The original
> list assumed you can change how the Drobo behaves. You can't: security takes
> two ends, and the Drobo's end is frozen firmware. A modern client cannot
> negotiate TLS with a device that doesn't speak it, or use session tokens with
> a device that doesn't issue them. **On stock firmware these are client-side
> and network-side measures only.** Anything requiring the device to change is
> §9 fork territory.

### Included

- ~~Modern TLS~~ → **TLS between the agent and the apps**, where you own both
  ends and it's meaningful. Device-side TLS requires custom firmware (§9).
- ~~Token-based sessions~~ → **credentials stored properly**: Windows Credential
  Manager on the PC, iOS Keychain on the phone. Device-side sessions are
  whatever the 2012 firmware does. Real token auth requires §9.
- **Secure local-only mode** — achievable and the single most important item
  here. LAN only, no port forwarding, ever.
- **Removal of myDrobo cloud relay dependency** — already gone whether you like
  it or not; Drobo's relay is dead. Replaced by Tailscale or WireGuard on a
  separate always-on device (router, Pi, the Windows PC) acting as a subnet
  router. It cannot run on the Drobo itself — kernel 3.2.96 predates
  WireGuard's mainline merge (Linux 5.6, 2020) by eight years, and the one
  community OpenVPN port has an open bug saying it won't start. Bonus: myDrobo
  capped around 8 Mbps; this won't.

### Excluded (back burner)

- VPN auto-connect
- Biometric unlock
- Remote cloud relay replacement

---

## 7. Back-Burner Features (Not Included Now)

Not part of the initial build; can be added later.

- Cloud backup integration
- AI photo indexing
- Health analytics & trend graphs
- Live Activities
- Widgets
- FaceID/TouchID unlock
- ~~Minimal web dashboard~~ → **built, and promoted out of back-burner**
- VPN auto-connect

> **Decision (2026-07-23):** the web dashboard got built early because it was
> the highest-value thing not blocked by Phase 1 or by needing a Mac. It's
> served by the agent at `/`, it's the fastest route to a Drobo UI on the
> iPhone (Safari, today, no Xcode), and it doubles as the layout prototype for
> the WinUI 3 app.

---

## 8. Development Timeline

| Phase | Original estimate |
|---|---|
| Phase 1 — Protocol Reverse Engineering | 3–4 weeks |
| Phase 2 — Unified SDK / Agent Development | 8–10 weeks |
| Phase 3 — Windows App Development | 10–14 weeks |
| Phase 4 — iOS App Development | 10–14 weeks |
| **Total** | **~6 months** |

> **Two adjustments.**
>
> **Phase 1 is a research task, not a build task.** You don't know what you'll
> find until you look — it could be readable XML over a socket (days) or an
> undocumented binary protocol (months). Nothing downstream can be scheduled
> until it produces answers, so treat 3–4 weeks as a placeholder, not a plan.
>
> **Re-sequence for an early win.** Don't build the whole SDK, then the whole
> Windows app. Build the thinnest slice that does something real — *agent reads
> drive health → Windows app displays it* — and grow. If Phase 1 goes badly you
> find out in week 3 having spent three weeks, not in month four.

### Working order

1. Capture and document the protocol → `docs/protocol-map.md`
2. Agent: connect, read status, expose one JSON endpoint
3. Windows app: one window showing real drive health from the real 5N
4. Agent: continuous polling, history, alert rules, ntfy push
5. Windows app: capacity, alerts, file browser, transfer manager, MSI +
   auto-updater
6. iOS app: direct SMB mode, then agent-assisted health and alerts
7. iOS app: photo backup, share sheet, background transfers

The ~6 month figure assumes full-time work. Part-time, expect materially longer.

---

## 9. Future Plans — Custom Firmware Fork

**Decision (2026-07-23):** replacing the Drobo's own firmware is a legitimate
long-term goal, but it is **not part of this project**. It gets its own fork,
started only after v1 ships and the protocol is fully understood.

**Why a separate fork rather than a later phase:** the two efforts have opposite
risk profiles. This project is read-mostly, reversible, and safe to run against
your live array. Firmware work can permanently destroy a working Drobo and the
data on it. They should never share a branch, a release, or a test device.

### What custom firmware would unlock

Everything §6 had to cut, plus more:

- Real TLS and token authentication at the device end
- A modern SMB server (SMBv3 instead of the 5N's SMBv2)
- WireGuard on the device itself, removing the need for a separate subnet router
- A current kernel — the stock 3.2.96 dates from 2013 and will never be patched
- A documented, stable API, so the reverse-engineered protocol stops mattering

### The blocker to solve first: BeyondRAID

This is the thing that decides whether the fork is viable at all.

BeyondRAID is Drobo's proprietary, undocumented on-disk format. It is not
mdadm, not ZFS, not LVM. Custom firmware that boots a modern kernel **will not
be able to read your existing disk pack** unless BeyondRAID is reverse
engineered first — which is a substantially harder problem than the management
protocol, and one nobody appears to have solved publicly.

That leaves two honest paths:

- **(a) Keep BeyondRAID.** Replace only the userland around Drobo's storage
  stack. Limited gains, much lower risk.
- **(b) Abandon BeyondRAID.** Reformat the pack under mdadm/ZFS and treat the
  5N as generic ARM NAS hardware. Far more capable, and it **destroys all data
  on the pack.** Only viable on a sacrificial unit.

### Hard prerequisites before any firmware work starts

1. **A second, sacrificial Drobo 5N.** Never the unit holding your data. These
   are cheap secondhand now precisely because the company died.
2. **A scratch disk pack** for that unit — drives you can lose.
3. **A verified full backup** of the real array on non-Drobo storage. Note that
   a BeyondRAID pack is only readable by another compatible Drobo, so a dead
   unit strands the data regardless of firmware.
4. **Serial console (UART) access** to the sacrificial unit, and a documented
   recovery path, *before* writing anything. Without a way to interrupt the
   bootloader and reflash, the first mistake is the last one.
5. **The bootloader mapped** — what it is, whether it verifies signatures, and
   whether it can be made to boot an unsigned image at all. If it enforces
   signature checks with no escape, the fork ends here.

### Existing footholds

- `github.com/droboports/kernel-drobo5n` — kernel source for this exact model.
  The single most useful starting point.
- `github.com/dhomas1/DroboApps` — the preserved DroboApps archive for SDK 2.x,
  which is how code legitimately runs on the device today. Getting a shell via
  a DroboApp is the low-risk way to explore the running system before touching
  the boot path.
- `droboports.com` — community package repo and port assignments.
- The Marvell Armada XP is a well-documented, mainline-supported SoC, which is
  the most encouraging fact about this whole idea.

### Sequencing

Do not start until: v1 ships, `docs/protocol-map.md` is complete, a sacrificial
unit is on the bench, and the backup in prerequisite 3 has been *restored from*
at least once to prove it works.

---

## Summary

A complete modernization of the Drobo software ecosystem for the **Drobo 5N**:

- Unified SDK or Drobo Agent *(decided 2026-07-23 — the Python HTTP/JSON agent, built; the protocol code then became its own package, `sdk/drobo_nasd`, on 2026-07-27 — §1)*
- Windows app
- iOS app
- Protocol documentation
- Reverse engineering workflow
- Security posture modernization

Plus the selected optional features:

- Dark Mode
- Push Notifications
- Share Sheet Integration
- Background Transfer Manager
- Local Thumbnail Generation
- Modern Installer / Auto-Updater — **promoted to core scope**

Cut from v1: firmware updating. Deferred to a separate fork: custom firmware
(§9). Everything else is back-burnered per §7.
