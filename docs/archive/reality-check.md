# Reality check on the Modernization Blueprint

The blueprint is solid. This document only covers the places where it promises
something the hardware or the platforms won't actually allow, plus one
architecture choice that would cost months for no benefit. Everything not
mentioned here stands as written.

Status of the sources: verified live in July 2026 unless marked otherwise.

Every correction below was accepted on 2026-07-23 and shaped what followed.
What was then built on those decisions is in [`development-history.md`](../development-history.md);
where each capability stands today is in [`capabilities.md`](../capabilities.md).

---

## 0. The clock is ticking on the one thing you can't replace

Drobo Dashboard 3.5.0 for Windows (released 2 Dec 2019, the final Windows
build) still installs and runs on Windows 11 today. That matters more than
anything else in this project, because **the running Dashboard is now the only
surviving specification of the Drobo protocol.** Drobo's servers are gone:
`www.drobo.com`, `supportportal.drobo.com` and `myproducts.drobo.com` all fail
DNS resolution outright. There is no documentation to fall back on.

The day a Windows update breaks Dashboard's installer, driver, or discovery, the
protocol becomes much harder to recover. So:

> **Do the packet capture first, before writing a single line of app code.**
> Capture it once, capture it thoroughly, and keep the captures somewhere
> durable **outside this repository** — `docs/captures/` is gitignored on
> purpose. This project is headed for a public release (Phase 6), and an
> unencrypted protocol means a capture can carry the Drobo's serial, disk
> serials, and even a password typed during the session, in plain text. Back
> the captures up to your own storage, never to a repo that will go public.

The blueprint already sequences reverse engineering as Phase 1 — this is just
underlining *why* it can't slip. Also: back up your Dashboard installer and the
installed `Program Files` directory now, and don't uninstall it. Community
mirrors exist (the Internet Archive item `archive.org/details/drobo`, and the
GitHub repo `jgauthiertech/DroboApps-` which preserves the final 3.5.0 Windows
installer) but relying on a stranger's mirror is worse than having your own.

---

## 1. Drop "Firmware update support" from scope

> **Resolved 2026-07-23 — accepted.** Firmware updating is cut from v1. Writing
> *custom* firmware is kept as a long-term goal in a separate fork; see
> [`BLUEPRINT.md` §9](BLUEPRINT.md#9-future-plans--custom-firmware-fork).

**Blueprint says:** Windows app core feature — firmware update support.

**Reality:** No new Drobo firmware will ever be released. The last builds were
4.3.0 (April 2022) and 4.3.1 (May 2022) for the 5N / 5N2 / B810n — and those
were *pulled by Drobo's own engineers shortly after release because of
user-reported problems*. The community's recommended last-known-good version is
**4.2.2**. Drobo filed for bankruptcy the following month and never shipped a
fix. Drobo FS topped out at 1.2.7 back in August 2015.

So the feature would be: a home-built flasher, writing an unsigned image
recovered from a third-party mirror, to the boot storage of an irreplaceable
device, with no vendor recovery tool if it goes wrong — in order to install
firmware that is either the one you already have or a version known to be buggy.

**Do instead:** the app *reads and displays* the firmware version, and warns if
you're on 4.3.x. Flashing stays a manual job with the old Dashboard, on the rare
occasion it's needed. This removes the only feature in the blueprint that can
destroy your hardware.

---

## 2. "Modern TLS / token-based sessions" can't be done client-side

> **Resolved 2026-07-23 — accepted.** Device-side TLS and token auth are
> deferred to the custom-firmware fork, since replacing the firmware is the only
> way to change the Drobo's end of the conversation. The client-side and
> network-side measures below are v1 scope.

**Blueprint says:** Security Modernization — modern TLS, token-based sessions.

**Reality:** Both ends of a connection have to agree on the security. The Drobo's
end is frozen firmware you cannot change. Your new app can't negotiate TLS with
a device that doesn't speak it, and can't use session tokens with a device that
doesn't issue them. Writing a beautiful modern client does not upgrade the
1990s-era protocol on the other side of the wire.

**Do instead — this is achievable and genuinely worth doing:**

- **Local network only, by default.** No port forwarding, ever. The device gets
  no security patches; anything reachable from the internet is a liability.
- **Store credentials properly** — Windows Credential Manager on the PC, the iOS
  Keychain on the phone. This is a real improvement over what Dashboard did.
- **Remote access via Tailscale or WireGuard,** which replaces the dead myDrobo
  relay and does it better. Important detail: it **cannot run on the Drobo
  itself** — a Drobo 5N is a Marvell Armada XP (ARMv7) running Linux kernel
  3.2.96, which predates WireGuard's kernel merge in Linux 5.6 (2020), and the
  one community OpenVPN port has an open bug saying it won't even start. Run
  Tailscale on a separate always-on box (router, Raspberry Pi, the Windows PC)
  as a subnet router; the phone then reaches the Drobo's normal LAN address from
  anywhere. As a bonus, myDrobo's relay capped out around 8 Mbps — this won't.
- **Modern TLS between your own two apps** (see §4), where you control both ends
  and it's actually meaningful.

Retitle the section "Security posture modernization" and it becomes honest.

---

## 3. iOS push notifications need a server. The Drobo can't be one.

**Blueprint says:** iOS optional feature — real-time alerts via APNs.

**Reality:** Apple Push Notification service only accepts pushes from a server
holding your developer credentials. The Drobo can't talk to APNs, and iOS won't
let your app sit in the background polling a NAS for drive failures — background
refresh is throttled to whatever the OS feels like, which may be hours. "Your
drive failed six hours ago" is not an alert.

**Do instead:** something has to be awake and watching. That's the agent in §4.
For v1, have the agent push through **ntfy** (free, self-hostable, has an iOS
app) or **Pushover** (one-time fee) instead of building APNs infrastructure —
you get real alerts in an afternoon rather than a fortnight, and you can swap in
your own APNs pipeline later once the agent exists and works.

Same idea makes the Windows toasts trivial, since the agent is already running
on Windows.

---

## 4. The big one: don't build a cross-language SDK with FFI bindings

**Blueprint says:** a unified SDK in Go or Rust, with bindings for Swift and C#,
as the single source of truth.

**Reality:** this is the standard architecture for a team shipping a commercial
product, and it's the wrong shape for this project. The binding layer is the
expensive part: Go's mobile bindings are effectively unmaintained; Rust's are
better (UniFFI) but you'd still be maintaining a Rust core, two generated
binding layers, two toolchains, an iOS XCFramework build, a NuGet package, and
CI for all of it — before the first useful screen exists. Meanwhile the actual
protocol surface here is small: read a status blob, parse it, list some files,
run a few diagnostics.

**Do instead — a small always-on "Drobo Agent" that speaks HTTP/JSON:**

```
   Drobo NAS  <--(the old proprietary protocol, spoken in exactly one place)-->
       Drobo Agent   (one program, one language, runs on the Windows PC or a Pi)
       |
       +---- REST + WebSocket, JSON, TLS ----> Windows app (WinUI 3)
       +---- REST + WebSocket, JSON, TLS ----> iOS app (SwiftUI)
```

This collapses several blueprint problems into one solution:

| Problem | How the agent solves it |
|---|---|
| Two languages need the protocol | Only the agent speaks it. Apps speak plain JSON. |
| No FFI/binding toolchain | Gone entirely. |
| iOS push needs a server | The agent *is* the server. |
| Remote access needs a relay | Agent + Tailscale, no relay. |
| Protocol changes ripple everywhere | One file, one language, one place to fix. |
| Nothing is watching for failures | The agent polls continuously and keeps history. |

The one real cost: the iOS app's *telemetry* features need the agent to be
awake. Mitigate by giving the iOS app two modes — **direct** (browse and
transfer files straight over SMB, works with nothing else running) and
**agent-assisted** (health, alerts, push, history). Ship direct first; it's the
mode people use most anyway.

> **Resolved 2026-07-23 — went the other way.** The agent is standard-library
> Python (`agent/drobo_agent`), the SDK is Python (`sdk/drobo_nasd`), the app
> is WPF, and the installer bundles a Python runtime. The reasoning below is
> kept as written.

Language for the agent: Go or Rust both fine, but **C# on .NET 8 is the better
pick here** — the Windows app is already .NET, so it's one language and one
toolchain for two of the three components, and it runs fine on a Raspberry Pi if
you later move it off the PC.

---

## 5. iOS specifics the blueprint doesn't budget for

- **The old apps are gone.** Confirmed by direct query to Apple's API: Drobo
  Access (id 1092803136) and DroboPix (id 1128168682) both return zero results
  and their App Store pages 404. There is no app to "modernize" — this is a
  from-scratch rewrite. The Android DroboPix listing is also 404.
- **$99/year, non-optional.** A free Apple ID signs an app for 7 days before it
  stops launching. The blueprint's "TestFlight builds, CI/CD" line requires a
  paid Apple Developer Program membership. Budget it.
- **Sideloading the old IPA is a dead end**, and not just legally: AltStore PAL
  is EU/Japan/Brazil only, and even a perfectly sideloaded DroboAccess would try
  to reach a cloud relay that no longer exists.
- **A lot of "file browser" value already exists for free.** Since iOS 13, the
  built-in Files app has an SMB client. A 5N (SMBv2) or 5N2/B810n (SMBv3) mounts
  natively with zero software. A Drobo FS is SMBv1 only and will *not* — that's
  the one case where a custom app genuinely adds file access. Aim the iOS app at
  what Files can't do: **health, alerts, and DroboPix-style automatic photo
  backup.** For the file plumbing, `AMSMB2` (a maintained Swift wrapper around
  libsmb2) saves you writing an SMB client.

For reference, DroboPix's original trick was smarter than it sounds: it only
auto-uploaded when the phone saw both your home Wi-Fi SSID *and* was inside a
geofence, and only over Wi-Fi. Worth copying. Its transfers were local-network
only, so nothing about that design depended on Drobo's dead cloud.

---

## 6. Timeline

The blueprint's ~6 months assumes full-time work with the protocol already
understood. Two adjustments:

- **Phase 1 is not 3-4 weeks of fixed work — it's the project's main risk.** You
  don't know what you'll find until you look. It could be readable XML over a
  socket (days), or an undocumented binary protocol over a proprietary driver
  (months). Everything downstream is unschedulable until Phase 1 produces
  answers, so treat its estimate as a placeholder.
- **Re-sequence so you get a win early.** Don't build the SDK, then the Windows
  app, then the iOS app. Build the thinnest vertical slice that does something
  real — *agent reads drive health, Windows app displays it* — and grow from
  there. If Phase 1 turns out badly, you find out in week 3 with a small
  investment, not in month 4.

Suggested order:

1. Capture and document the protocol. *(→ `docs/archive/phase-1-capture-playbook.md`)*
2. Agent: connect, read status, expose one JSON endpoint.
3. Windows app: one window showing real drive health from your real Drobo.
4. Agent: continuous polling, history, alert rules, ntfy push.
5. Windows app: capacity, alerts, file browser, transfer manager, installer.
6. iOS app: direct SMB mode first, then agent-assisted health and alerts.
7. iOS app: photo backup, share sheet, background transfers.

---

## Answered: the target is a Drobo 5N

**Resolved 2026-07-23.** This is the good case. The 5N is a network model, so
Drobo Dashboard talks to it over Ethernet and ordinary Wireshark can see every
byte of the conversation. No USB/SCSI reverse engineering, no proprietary
driver in the way.

What that pins down:

- **Phase 1 is straight network capture.** The USB path in the capture playbook
  (Step 5) does not apply.
- **SMBv2**, so the iOS Files app mounts shares natively — which is why the iOS
  app should aim at health, alerts and photo backup rather than file browsing.
- **DroboApps work** (SDK 2.x), which is the sanctioned way to run code on the
  device and the low-risk way to explore it.
- **Marvell Armada XP, ARMv7, Linux 3.2.96** — old, but a mainline-supported
  SoC, which matters for the firmware fork later.

*(Answered 2026-07-24, on the first live connection: the unit runs 4.3.1, one
of the two builds Drobo withdrew, and the app shows a one-time advisory for
it.)* Remaining hardware unknown, as first written: the current **firmware
version**, which comes off Dashboard's status page in Step 0 of the playbook. If it reads 4.3.0 or 4.3.1,
note it — those were pulled by Drobo and 4.2.2 is the last known-good.
